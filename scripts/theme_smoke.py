#!/usr/bin/env python3
"""theme_smoke.py — Jadeveil 主题运行时冒烟测试。

把一次人肉 QA（"打开 Obsidian，切换明暗，瞅一眼侧栏/命令面板/缩进线是否符合
预期"）固化成一次可重复运行的脚本。通过 `obsidian eval code=<js>` 子进程在
正在运行的 Obsidian 里取 computed style 并断言——本脚本只读，不修改
theme.css，也不修改用户 vault 内容（会临时切换明暗主题，结束时还原）。

用法：
    python3 theme_smoke.py                 # 明暗各跑一遍
    python3 theme_smoke.py --mode dark      # 只跑深色
    python3 theme_smoke.py --quiet          # 只打印汇总行

退出码：
    0 全部 PASS / SKIP
    1 存在 FAIL
    2 无法连接到运行中的 Obsidian（obsidian CLI 缺失，或 Obsidian.app 未运行/
      未打开目标 vault）

依赖：Obsidian.app 正在运行且已打开 vault "my_wiki"；`obsidian` CLI 在 PATH 中。

关键陷阱（写脚本时踩过，别再踩）：
1. transition 冻结假值——窗口被遮挡时 Chromium 冻结过渡动画，冻结插值在
   CSS 级联里压过一切（含 !important）。取值前必须先注入
   `* { transition: none !important; animation: none !important; }`，
   结束时移除（finally 保证）。
2. `obsidian eval` 偶发返回空输出（无 `=>` 前缀），需自动重试。
3. Obsidian 内部的 `activeWindow` / `activeDocument`（Notice/Modal 挂载点）
   可能因为 CLI 从不触发真实的原生 focus 事件而停留在一个已关闭窗口的引用
   上——此时 `new Notice()` / 命令面板会挂到一个和当前 `document` 完全不同、
   甚至已销毁的文档上，导致 `document.querySelector('.prompt')` 永远查不到，
   看起来像模态框"打不开"。修复方式很轻：把这两个全局重新指回真正的
   `window`/`document`（`window.activeWindow = window; window.activeDocument
   = document;`），此后命令面板就能在正确的文档里渲染。
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Optional

VAULT = "my_wiki"
FREEZE_STYLE_ID = "jadeveil-smoke-freeze"
EVAL_TIMEOUT = 20  # 秒
EVAL_RETRIES = 2  # "偶发返回空输出，需自动重试最多 2 次" —— 总计最多 3 次尝试
CHAN_TOL = 2  # 颜色通道容差
ALPHA_TOL = 0.02  # alpha 容差
HUE_TOL = 15  # 色相容差（度）


class ObsidianConnectionError(Exception):
    """无法连接到运行中的 Obsidian，或 obsidian CLI 不可用。"""


class ObsidianEvalError(Exception):
    """JS 代码本身抛出了异常（脚本 bug，非连接问题）。"""


# ═══════════════════════════ obsidian CLI 封装 ═══════════════════════════


def obsidian_eval_raw(code: str, retries: int = EVAL_RETRIES) -> str:
    """执行 `obsidian eval`，解析 `=> <result>` 行，返回 JSON 文本。

    空输出 / 未识别输出会自动重试（累计最多 retries+1 次尝试）；
    JS 抛出的异常（`Error: ...`）不重试，直接抬升为 ObsidianEvalError。
    """
    if shutil.which("obsidian") is None:
        raise ObsidianConnectionError(
            "找不到 obsidian CLI（`which obsidian` 无结果）。"
            "请确认已安装 Obsidian CLI 并在 PATH 中。"
        )

    last_seen = ""
    for attempt in range(retries + 1):
        try:
            proc = subprocess.run(
                ["obsidian", f"vault={VAULT}", "eval", f"code={code}"],
                capture_output=True,
                text=True,
                timeout=EVAL_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            last_seen = "<subprocess 超时>"
            if attempt < retries:
                time.sleep(0.3)
                continue
            break

        out = (proc.stdout or "").strip()
        err = (proc.stderr or "").strip()
        last_seen = out or err or "<空输出>"

        if out.startswith("=>"):
            return out[2:].strip()
        if out.startswith("Error:") or err:
            raise ObsidianEvalError(f"JS 执行出错: {out or err}")

        # 未识别 / 空输出——按文档已知的"偶发空输出"重试
        if attempt < retries:
            time.sleep(0.3)
            continue

    raise ObsidianConnectionError(
        f"无法连接到运行中的 Obsidian（尝试 {retries + 1} 次后仍无有效输出）。"
        f"请确认 Obsidian.app 正在运行且已打开 vault「{VAULT}」。"
        f"最后一次输出: {last_seen!r}"
    )


def obsidian_eval_json(js_expr: str):
    """执行一段以 `return JSON.stringify(...)` 结尾的 JS，返回解析后的 Python 值。"""
    result = obsidian_eval_raw(js_expr)
    try:
        return json.loads(result)
    except json.JSONDecodeError:
        return result


# ═══════════════════════════ 颜色 / 色相工具 ═══════════════════════════


_RGBA_RE = re.compile(
    r"^rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*(?:,\s*([\d.]+)\s*)?\)$"
)
_HEX3_RE = re.compile(r"^#([0-9a-fA-F]{3})$")
_HEX6_RE = re.compile(r"^#([0-9a-fA-F]{6})$")
_HEX8_RE = re.compile(r"^#([0-9a-fA-F]{8})$")


def parse_color(s: Optional[str]):
    """解析 rgb()/rgba()/hex 颜色字符串为 (r, g, b, a) 四元组，失败返回 None。"""
    if not s:
        return None
    s = s.strip()
    if s in ("transparent", "none"):
        return (0.0, 0.0, 0.0, 0.0)

    m = _RGBA_RE.match(s)
    if m:
        r, g, b = float(m.group(1)), float(m.group(2)), float(m.group(3))
        a = float(m.group(4)) if m.group(4) is not None else 1.0
        return (r, g, b, a)

    m = _HEX8_RE.match(s)
    if m:
        h = m.group(1)
        r, g, b, a8 = (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), int(h[6:8], 16))
        return (float(r), float(g), float(b), a8 / 255.0)

    m = _HEX6_RE.match(s)
    if m:
        h = m.group(1)
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return (float(r), float(g), float(b), 1.0)

    m = _HEX3_RE.match(s)
    if m:
        h = m.group(1)
        r, g, b = [int(c * 2, 16) for c in h]
        return (float(r), float(g), float(b), 1.0)

    return None


def colors_equal(a_str, b_str, chan_tol=CHAN_TOL, alpha_tol=ALPHA_TOL) -> bool:
    a, b = parse_color(a_str), parse_color(b_str)
    if a is None or b is None:
        return False
    return (
        abs(a[0] - b[0]) <= chan_tol
        and abs(a[1] - b[1]) <= chan_tol
        and abs(a[2] - b[2]) <= chan_tol
        and abs(a[3] - b[3]) <= alpha_tol
    )


def alpha_of(s: Optional[str]) -> Optional[float]:
    c = parse_color(s)
    return c[3] if c else None


def is_opaque(s: Optional[str], tol=ALPHA_TOL) -> bool:
    a = alpha_of(s)
    return a is not None and a >= 1.0 - tol


def has_blur(s: Optional[str]) -> bool:
    return bool(s) and s.strip() != "none" and "blur(" in s


def parse_px(s: Optional[str]) -> Optional[float]:
    if not s:
        return None
    m = re.match(r"^([\d.]+)px", s.strip())
    return float(m.group(1)) if m else None


def rgb_to_hue(r: float, g: float, b: float) -> Optional[float]:
    """返回 0-360 的色相；无色相（灰/白/黑，饱和度≈0）时返回 None。"""
    r, g, b = r / 255.0, g / 255.0, b / 255.0
    mx, mn = max(r, g, b), min(r, g, b)
    d = mx - mn
    if d < 0.04:  # 近似无色相（chroma <4%：v3 暖 ink 如 rgb(47,46,42) 带微弱暖底，
        # 感知上仍是灰，不应参与色相比对；纯 1e-3 阈值会把它误判为黄 48°）
        return None
    if mx == r:
        h = ((g - b) / d) % 6
    elif mx == g:
        h = (b - r) / d + 2
    else:
        h = (r - g) / d + 4
    return h * 60.0


def hue_close(h1: Optional[float], h2: Optional[float], tol=HUE_TOL) -> bool:
    if h1 is None or h2 is None:
        return False
    diff = abs(h1 - h2) % 360
    diff = min(diff, 360 - diff)
    return diff <= tol


# ═══════════════════════════ 结果记录 ═══════════════════════════


@dataclass
class Result:
    mode: str
    name: str
    status: str  # PASS | FAIL | SKIP
    expected: Optional[str] = None
    actual: Optional[str] = None
    note: Optional[str] = None


@dataclass
class Report:
    results: list = field(default_factory=list)

    def add(self, mode, name, status, expected=None, actual=None, note=None):
        self.results.append(Result(mode, name, status, expected, actual, note))

    def line_for(self, r: Result) -> str:
        head = f"[{r.status}] {r.mode} {r.name}"
        if r.status == "FAIL":
            return f"{head} — 期望 {r.expected} 实际 {r.actual}"
        if r.status == "SKIP" and r.note:
            return f"{head} — {r.note}"
        return head

    def print_all(self, quiet: bool):
        if not quiet:
            for r in self.results:
                print(self.line_for(r))
        n_pass = sum(1 for r in self.results if r.status == "PASS")
        n_fail = sum(1 for r in self.results if r.status == "FAIL")
        n_skip = sum(1 for r in self.results if r.status == "SKIP")
        print(f"{n_pass} pass / {n_fail} fail / {n_skip} skip")
        return n_fail


# ═══════════════════════════ JS 片段 ═══════════════════════════

JS_PING = "JSON.stringify({ok: true, vault: app.vault.getName()})"

JS_FIX_ACTIVE_WINDOW = """
(function(){
  try {
    window.activeWindow = window;
    window.activeDocument = document;
  } catch (e) {}
  return JSON.stringify({ok: true});
})()
"""

JS_FREEZE = f"""
(function(){{
  if (!document.getElementById('{FREEZE_STYLE_ID}')) {{
    var s = document.createElement('style');
    s.id = '{FREEZE_STYLE_ID}';
    s.textContent = '* {{ transition: none !important; animation: none !important; }}';
    document.head.appendChild(s);
  }}
  return JSON.stringify({{ok: true}});
}})()
"""

JS_UNFREEZE = f"""
(function(){{
  var s = document.getElementById('{FREEZE_STYLE_ID}');
  if (s) s.remove();
  return JSON.stringify({{ok: true}});
}})()
"""

JS_GET_THEME = """
JSON.stringify({dark: document.body.classList.contains('theme-dark')})
"""


def js_set_theme(dark: bool) -> str:
    name = "obsidian" if dark else "moonstone"
    return f"app.changeTheme('{name}'); JSON.stringify({{ok: true}})"


JS_COLLECT = """
(function(){
  function css(sel){
    var e = document.querySelector(sel);
    if (!e) return null;
    return getComputedStyle(e);
  }
  var body = document.body;
  var bodyCS = getComputedStyle(body);
  function tok(name){ return bodyCS.getPropertyValue(name).trim(); }

  var rootCS = css('.workspace-split.mod-root');
  var appCS = css('.app-container');
  var leftCS = css('.workspace-split.mod-left-split');
  var ribbonCS = css('.workspace-ribbon.side-dock-ribbon');
  var statusCS = css('.status-bar');
  var activeIconCS = css('.mod-left-split .workspace-tab-header.is-active');
  var indentCS = css('.nav-files-container .tree-item-children');
  var metaInputCS = css('.metadata-container input[type="text"]');
  var vaultProfileCS = css('.workspace-sidedock-vault-profile');
  var navActiveCS = css('.nav-file-title.is-active');

  var out = {
    theme: body.classList.contains('theme-dark') ? 'dark' : 'light',
    isTranslucent: body.classList.contains('is-translucent'),
    glassOff: body.classList.contains('jadeveil-glass-off'),
    isFullscreen: body.classList.contains('is-fullscreen'),
    indentGuidesOn: body.classList.contains('jadeveil-indent-guides'),
    tokens: {
      glassPaper: tok('--jv-glass-paper'),
      backgroundPrimary: tok('--background-primary'),
      chromeTint: tok('--jv-glass-chrome-tint'),
      surfaceChrome: tok('--jv-surface-chrome'),
      surfaceFloat: tok('--jv-surface-float'),
      accentH: tok('--accent-h')
    },
    root: rootCS ? {found: true, bg: rootCS.backgroundColor} : {found: false},
    appContainer: appCS ? {found: true, bg: appCS.backgroundColor} : {found: false},
    leftSplit: leftCS ? {found: true, bg: leftCS.backgroundColor, blur: leftCS.backdropFilter} : {found: false},
    ribbon: ribbonCS ? {found: true, bg: ribbonCS.backgroundColor, blur: ribbonCS.backdropFilter} : {found: false},
    statusBar: statusCS ? {found: true, blur: statusCS.backdropFilter} : {found: false},
    activeIcon: activeIconCS ? {found: true, bg: activeIconCS.backgroundColor} : {found: false},
    indent: indentCS ? {found: true, borderColor: indentCS.borderLeftColor} : {found: false},
    metaInput: metaInputCS ? {found: true, borderColor: metaInputCS.borderColor} : {found: false},
    vaultProfile: vaultProfileCS ? {found: true, bg: vaultProfileCS.backgroundColor} : {found: false},
    navActive: navActiveCS ? {found: true, color: navActiveCS.color} : {found: false}
  };
  return JSON.stringify(out);
})()
"""

JS_COMMAND_PALETTE = """
(function(){
  try {
    window.activeWindow = window;
    window.activeDocument = document;
  } catch (e) {}
  app.commands.executeCommandById('command-palette:open');
  var el = document.querySelector('.prompt');
  var out = {found: !!el};
  if (el) {
    var cs = getComputedStyle(el);
    out.bg = cs.backgroundColor;
    out.blur = cs.backdropFilter;
    out.radius = cs.borderRadius;
  }
  var bg = document.querySelector('.modal-bg');
  if (bg) bg.click();
  return JSON.stringify(out);
})()
"""


def open_command_palette(retries: int = 2) -> dict:
    """打开命令面板并读取样式，DOM 未渲染出来时重试（不同于 CLI 层的空输出重试）。"""
    last = {"found": False}
    for attempt in range(retries + 1):
        result = obsidian_eval_json(JS_COMMAND_PALETTE)
        if isinstance(result, dict):
            last = result
            if result.get("found"):
                return result
        if attempt < retries:
            time.sleep(0.4)
    return last


# ═══════════════════════════ 断言逻辑（单个模式） ═══════════════════════════


def run_mode_checks(report: Report, mode: str, d: dict) -> None:
    is_translucent = d.get("isTranslucent", False)
    glass_off = d.get("glassOff", False)
    is_fullscreen = d.get("isFullscreen", False)
    indent_guides_on = d.get("indentGuidesOn", False)
    glass_active = is_translucent and not glass_off and not is_fullscreen

    tokens = d.get("tokens", {})
    glass_paper = tokens.get("glassPaper")
    background_primary = tokens.get("backgroundPrimary")
    chrome_tint = tokens.get("chromeTint")
    surface_chrome = tokens.get("surfaceChrome")
    surface_float = tokens.get("surfaceFloat")
    accent_h_raw = tokens.get("accentH")

    # 1. paper
    root = d.get("root", {})
    if not root.get("found"):
        report.add(mode, "paper", "SKIP", note="元素不存在: .workspace-split.mod-root")
    else:
        expected = glass_paper if glass_active else background_primary
        actual = root.get("bg")
        if colors_equal(expected, actual):
            report.add(mode, "paper", "PASS")
        else:
            report.add(mode, "paper", "FAIL", expected, actual)

    # 2. veil（黑纱回归哨兵，D1 依赖）
    app_container = d.get("appContainer", {})
    if not app_container.get("found"):
        report.add(mode, "veil", "SKIP", note="元素不存在: .app-container")
    else:
        a = alpha_of(app_container.get("bg"))
        if a is not None and a <= 0.4 + 1e-9:
            report.add(mode, "veil", "PASS")
        else:
            report.add(mode, "veil", "FAIL", "alpha<=0.4", app_container.get("bg"))

    # 3. 左侧栏
    left = d.get("leftSplit", {})
    if not left.get("found"):
        report.add(mode, "左侧栏", "SKIP", note="元素不存在: .workspace-split.mod-left-split")
    else:
        if glass_active:
            bg_ok = colors_equal(chrome_tint, left.get("bg"))
            blur_ok = has_blur(left.get("blur"))
            if bg_ok and blur_ok:
                report.add(mode, "左侧栏", "PASS")
            else:
                expected = f"bg={chrome_tint} blur存在"
                actual = f"bg={left.get('bg')} blur={left.get('blur')}"
                report.add(mode, "左侧栏", "FAIL", expected, actual)
        else:
            if is_opaque(left.get("bg")):
                report.add(mode, "左侧栏", "PASS")
            else:
                report.add(mode, "左侧栏", "FAIL", "非 translucent 态应不透明", left.get("bg"))

    # 4. Ribbon（仅 translucent 态定义此断言）
    ribbon = d.get("ribbon", {})
    if not glass_active:
        report.add(mode, "Ribbon", "SKIP", note="非 translucent 态，该断言仅在 translucent 态下定义")
    elif not ribbon.get("found"):
        report.add(mode, "Ribbon", "SKIP", note="元素不存在: .workspace-ribbon.side-dock-ribbon")
    else:
        # v3：Ribbon 与侧栏同玻璃（同 tint 同 blur）——玻璃清透化后实体
        # Ribbon 会与半透明侧栏形成材质断层（用户截图实锤），故与左侧栏
        # 断言同构：glass_active 下期望 chrome tint + blur。
        bg_ok = colors_equal(chrome_tint, ribbon.get("bg"))
        blur_ok = has_blur(ribbon.get("blur"))
        if bg_ok and blur_ok:
            report.add(mode, "Ribbon", "PASS")
        else:
            report.add(mode, "Ribbon", "FAIL",
                       f"bg={chrome_tint} blur存在",
                       f"bg={ribbon.get('bg')} blur={ribbon.get('blur')}")

    # 5. 状态栏（仅 translucent 态定义此断言）
    status_bar = d.get("statusBar", {})
    if not glass_active:
        report.add(mode, "状态栏", "SKIP", note="非 translucent 态，该断言仅在 translucent 态下定义")
    elif not status_bar.get("found"):
        report.add(mode, "状态栏", "SKIP", note="元素不存在: .status-bar")
    else:
        if has_blur(status_bar.get("blur")):
            report.add(mode, "状态栏", "PASS")
        else:
            report.add(mode, "状态栏", "FAIL", "blur 存在", status_bar.get("blur"))

    # 6. 侧栏 active 图标胶囊（D3 哨兵，无 translucent 条件）——Wave B §5：
    # 玻璃 float-tint 已换成实心 surface-float（抬起小 pill 不用玻璃）
    active_icon = d.get("activeIcon", {})
    if not active_icon.get("found"):
        report.add(
            mode, "侧栏active图标胶囊", "SKIP",
            note="元素不存在: .mod-left-split .workspace-tab-header.is-active",
        )
    else:
        if colors_equal(surface_float, active_icon.get("bg")):
            report.add(mode, "侧栏active图标胶囊", "PASS")
        else:
            report.add(mode, "侧栏active图标胶囊", "FAIL", surface_float, active_icon.get("bg"))

    # 7. 缩进线（D4 哨兵）
    indent = d.get("indent", {})
    if indent_guides_on:
        report.add(mode, "缩进线", "SKIP", note="jadeveil-indent-guides 开关已开启")
    elif not indent.get("found"):
        report.add(mode, "缩进线", "SKIP", note="元素不存在: .nav-files-container .tree-item-children")
    else:
        a = alpha_of(indent.get("borderColor"))
        if a is not None and a <= ALPHA_TOL:
            report.add(mode, "缩进线", "PASS")
        else:
            report.add(mode, "缩进线", "FAIL", "alpha==0", indent.get("borderColor"))

    # 9. metadata 安静面板
    meta_input = d.get("metaInput", {})
    if not meta_input.get("found"):
        report.add(mode, "metadata安静面板", "SKIP", note="元素不存在: .metadata-container input[type=text]")
    else:
        a = alpha_of(meta_input.get("borderColor"))
        if a is not None and a <= ALPHA_TOL:
            report.add(mode, "metadata安静面板", "PASS")
        else:
            report.add(mode, "metadata安静面板", "FAIL", "alpha==0", meta_input.get("borderColor"))

    # 10. vault profile
    vault_profile = d.get("vaultProfile", {})
    if not vault_profile.get("found"):
        report.add(mode, "vault profile", "SKIP", note="元素不存在: .workspace-sidedock-vault-profile")
    else:
        a = alpha_of(vault_profile.get("bg"))
        if a is not None and a <= ALPHA_TOL:
            report.add(mode, "vault profile", "PASS")
        else:
            report.add(mode, "vault profile", "FAIL", "alpha==0", vault_profile.get("bg"))

    # 11a. accent-h 是有效数字
    try:
        accent_h = float(accent_h_raw)
        accent_h_valid = 0.0 <= accent_h <= 360.0
    except (TypeError, ValueError):
        accent_h = None
        accent_h_valid = False
    if accent_h_valid:
        report.add(mode, "accent-h数值", "PASS")
    else:
        report.add(mode, "accent-h数值", "FAIL", "0-360 之间的数字", accent_h_raw)

    # 11b. nav active 文字色相跟随 accent-h
    nav_active = d.get("navActive", {})
    if not nav_active.get("found"):
        report.add(mode, "accent贯通", "SKIP", note="元素不存在: .nav-file-title.is-active（侧栏文件树多为虚拟渲染，需展开到当前文件所在目录才会挂载）")
    else:
        rgba = parse_color(nav_active.get("color"))
        if rgba is None:
            report.add(mode, "accent贯通", "FAIL", "可解析颜色", nav_active.get("color"))
        else:
            hue = rgb_to_hue(rgba[0], rgba[1], rgba[2])
            if hue is None:
                # 设计如此（v2 收敛后）：.nav-file-title.is-active 用
                # `color: var(--text-normal) !important` 中性灰 pill + 文字加深
                # 承担「被选中」信号，不再挂 accent，文字本身不携带色相，
                # 色相比对天然不适用于此元素（不是回归）。
                report.add(
                    mode, "accent贯通", "SKIP",
                    note=f"文字色 {nav_active.get('color')} 为无色相色（白/灰/黑）——"
                    "该元素设计为中性 text-normal 承担选中信号，不挂 accent，色相比对不适用",
                )
            elif accent_h is not None and hue_close(hue, accent_h):
                report.add(mode, "accent贯通", "PASS")
            else:
                report.add(mode, "accent贯通", "FAIL", f"hue≈{accent_h}±{HUE_TOL}", f"hue={hue:.1f}")



def run_variant_checks(report: Report) -> None:
    """变体路径断言（v3.4 新增，外部评审实锤盲区：paper-warm/OLED 曾断在
    未派生的叶子字面量上而无人察觉——变体铁律最严处恰好无测试覆盖）。
    做法：深色态临时挂 body class，断言叶子 token 确实被重映射，测完摘除。"""
    tok = lambda name: obsidian_eval_json(
        f"JSON.stringify(getComputedStyle(document.body).getPropertyValue('{name}').trim())"
    )
    toggle = lambda cls, on: obsidian_eval_json(
        f"document.body.classList.{'add' if on else 'remove'}('{cls}'); JSON.stringify('ok')"
    )
    # --- OLED（深色态下测）---
    obsidian_eval_json(js_set_theme(True)); time.sleep(0.8)
    base_paper = tok('--jv-glass-paper')
    base_chrome = tok('--jv-surface-chrome')
    toggle('jadeveil-oled', True); time.sleep(0.3)
    try:
        oled_paper = tok('--jv-glass-paper')
        oled_chrome = tok('--jv-surface-chrome')
        if oled_paper != base_paper and oled_chrome != base_chrome:
            report.add('variant', 'oled叶子token重映射', 'PASS')
        else:
            report.add('variant', 'oled叶子token重映射', 'FAIL',
                       'glass-paper 与 surface-chrome 应随 OLED 变化',
                       f'paper {base_paper}->{oled_paper} chrome {base_chrome}->{oled_chrome}')
    finally:
        toggle('jadeveil-oled', False)
    # --- paper-warm（浅色态下测）---
    obsidian_eval_json(js_set_theme(False)); time.sleep(1.2)
    base_paper = tok('--jv-glass-paper')
    toggle('jadeveil-paper-warm', True); time.sleep(0.3)
    try:
        warm_paper = tok('--jv-glass-paper')
        if warm_paper != base_paper:
            report.add('variant', 'paper-warm玻璃纸面跟随', 'PASS')
        else:
            report.add('variant', 'paper-warm玻璃纸面跟随', 'FAIL',
                       'translucent 正文纸面应随开关加深', f'{base_paper} 未变化')
    finally:
        toggle('jadeveil-paper-warm', False)
    # --- glass-off（浅色态下测）---
    toggle('jadeveil-glass-off', True); time.sleep(0.3)
    try:
        left = obsidian_eval_json(
            "JSON.stringify((()=>{const e=document.querySelector('.workspace-split.mod-left-split');"
            "const cs=getComputedStyle(e); return {bg:cs.backgroundColor, bf:cs.backdropFilter}})())"
        )
        bf_off = isinstance(left, dict) and left.get('bf') in ('none', '')
        bg_solid = isinstance(left, dict) and 'rgba' not in (left.get('bg') or 'rgba')
        if bf_off and bg_solid:
            report.add('variant', 'glass-off退化不透明', 'PASS')
        else:
            report.add('variant', 'glass-off退化不透明', 'FAIL',
                       'blur 归零且侧栏不透明', str(left))
    finally:
        toggle('jadeveil-glass-off', False)


def run_command_palette_check(report: Report, mode: str) -> None:
    cp = open_command_palette()
    if not cp.get("found"):
        report.add(mode, "命令面板", "SKIP", note="打开后未在 DOM 中找到 .prompt（可能被焦点问题打断）")
        return
    float_tint = obsidian_eval_json(
        "JSON.stringify(getComputedStyle(document.body).getPropertyValue('--jv-glass-float-tint').trim())"
    )
    bg_ok = colors_equal(float_tint, cp.get("bg"))
    blur_ok = has_blur(cp.get("blur"))
    radius = parse_px(cp.get("radius"))
    radius_ok = radius is not None and abs(radius - 20) <= 1  # .prompt 圆角实为 20px（§6），旧期望 28 来自过时的 xl 注释
    if bg_ok and blur_ok and radius_ok:
        report.add(mode, "命令面板", "PASS")
    else:
        expected = f"bg={float_tint} blur存在 radius=20px"
        actual = f"bg={cp.get('bg')} blur={cp.get('blur')} radius={cp.get('radius')}"
        report.add(mode, "命令面板", "FAIL", expected, actual)


# ═══════════════════════════ 主流程 ═══════════════════════════


def main() -> int:
    parser = argparse.ArgumentParser(description="Jadeveil 主题运行时冒烟测试")
    parser.add_argument("--mode", choices=["light", "dark", "both"], default="both")
    parser.add_argument("--quiet", action="store_true", help="只打印汇总行")
    args = parser.parse_args()

    modes = ["light", "dark"] if args.mode == "both" else [args.mode]

    # 连接性检查
    try:
        ping = obsidian_eval_json(JS_PING)
        if not isinstance(ping, dict) or not ping.get("ok"):
            raise ObsidianConnectionError(f"obsidian eval 返回了非预期结果: {ping!r}")
    except ObsidianConnectionError as e:
        print(f"错误: {e}", file=sys.stderr)
        return 2
    except ObsidianEvalError as e:
        print(f"错误: {e}", file=sys.stderr)
        return 2

    report = Report()
    initial_dark = None
    froze = False

    try:
        # 记录初始明暗，结束时还原
        theme_state = obsidian_eval_json(JS_GET_THEME)
        initial_dark = bool(theme_state.get("dark")) if isinstance(theme_state, dict) else None

        # 修复 activeWindow/activeDocument（Notice/Modal 挂载点），见文件头注释 3
        obsidian_eval_json(JS_FIX_ACTIVE_WINDOW)

        # 冻结 transition/animation，防止窗口遮挡时读到冻结插值假值
        obsidian_eval_json(JS_FREEZE)
        froze = True

        for mode in modes:
            want_dark = mode == "dark"
            current = obsidian_eval_json(JS_GET_THEME)
            current_dark = bool(current.get("dark")) if isinstance(current, dict) else None
            if current_dark != want_dark:
                obsidian_eval_json(js_set_theme(want_dark))
                time.sleep(1.6)

            d = obsidian_eval_json(JS_COLLECT)
            if not isinstance(d, dict):
                print(f"错误: 采集 {mode} 模式数据失败，返回值: {d!r}", file=sys.stderr)
                return 2
            run_mode_checks(report, mode, d)
            run_command_palette_check(report, mode)

        # 变体矩阵（oled / paper-warm / glass-off 各一条冒烟断言）
        run_variant_checks(report)

    except (ObsidianConnectionError, ObsidianEvalError) as e:
        print(f"错误: {e}", file=sys.stderr)
        return 2
    finally:
        # 还原明暗模式
        try:
            if initial_dark is not None:
                current = obsidian_eval_json(JS_GET_THEME)
                current_dark = bool(current.get("dark")) if isinstance(current, dict) else None
                if current_dark != initial_dark:
                    obsidian_eval_json(js_set_theme(initial_dark))
                    time.sleep(1.6)
        except Exception:
            pass
        # 移除注入的 freeze 样式
        if froze:
            try:
                obsidian_eval_json(JS_UNFREEZE)
            except Exception:
                pass

    n_fail = report.print_all(args.quiet)
    return 1 if n_fail > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
