#!/usr/bin/env python3
"""Jadeveil 主题静态 lint 脚本。

只读脚本：对 .obsidian/themes/Jadeveil/theme.css 做六项独立的静态检查，
每项各自 PASS / FAIL / WARN。基线文件 lint-baseline.json 记录「已知且已批准」
的 backdrop-filter 位置 / !important 台账 / 裸色值数量，供逐次运行做增量比对
——新增未登记的高风险改动即 FAIL，减少则提示更新基线。

用法：
  python3 theme_lint.py                    # 检查模式，默认读 theme.css
  python3 theme_lint.py --file <path>       # 检查模式，读指定文件（负样本自测用）
  python3 theme_lint.py --update-baseline   # 重算基线写入 lint-baseline.json

标准库 only（yaml 可用则用于 @settings 完整校验，不可用降级为结构检查）。
"""
from __future__ import annotations
import re, sys, json, bisect, datetime
from pathlib import Path

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
if (REPO_ROOT / 'theme.css').is_file():
    THEME_DIR = REPO_ROOT
else:
    VAULT_ROOT = SCRIPT_DIR.parent.parent  # .claude/scripts -> 上两级 = vault 根
    THEME_DIR = VAULT_ROOT / '.obsidian' / 'themes' / 'Jadeveil'
DEFAULT_THEME_CSS = THEME_DIR / 'theme.css'
BASELINE_PATH = THEME_DIR / 'lint-baseline.json'

TOKEN_PREFIX_RE = re.compile(r'^--(?:jv|jadeveil)-[A-Za-z0-9-]+$')

# ---------- 通用：注释/字符串剥离（保留长度与换行，供偏移量在剥离前后保持一致） ----------

def strip_comments_and_strings(text: str) -> str:
    out = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == '/' and i + 1 < n and text[i + 1] == '*':
            j = text.find('*/', i + 2)
            end = j + 2 if j != -1 else n
            out.append(''.join('\n' if ch == '\n' else ' ' for ch in text[i:end]))
            i = end
        elif c in ('"', "'"):
            quote = c
            j = i + 1
            while j < n and text[j] != quote:
                if text[j] == '\\':
                    j += 1
                j += 1
            end = min(j + 1, n)
            out.append(''.join('\n' if ch == '\n' else ' ' for ch in text[i:end]))
            i = end
        else:
            out.append(c)
            i += 1
    return ''.join(out)


def make_line_finder(text: str):
    offsets = [i for i, c in enumerate(text) if c == '\n']
    def line_no(pos: int) -> int:
        return bisect.bisect_right(offsets, pos) + 1
    return line_no


def normalize_ws(s: str) -> str:
    return re.sub(r'\s+', ' ', s).strip()


def find_enclosing_selector(text: str, pos: int) -> str:
    """从 pos 向前扫描，跳过已闭合的嵌套块，定位当前声明所在规则的（可能含 @media 外层的）选择器文本。"""
    depth = 0
    i = pos - 1
    open_brace_idx = None
    while i >= 0:
        c = text[i]
        if c == '}':
            depth += 1
        elif c == '{':
            if depth == 0:
                open_brace_idx = i
                break
            depth -= 1
        i -= 1
    if open_brace_idx is None:
        return ''
    j = open_brace_idx - 1
    while j >= 0 and text[j] not in '{}':
        j -= 1
    return text[j + 1:open_brace_idx].strip()


# ---------- 检查 1：括号配平 ----------

def check_bracket_balance(stripped: str, line_no):
    stack = []
    mismatches = []
    pairs_close = {')': '(', '}': '{'}
    for i, ch in enumerate(stripped):
        if ch in '{(':
            stack.append((ch, i))
        elif ch in '})':
            if not stack:
                mismatches.append(f'第{line_no(i)}行: 多余的 {ch!r}，无匹配开括号')
                continue
            top_ch, top_pos = stack.pop()
            if pairs_close[ch] != top_ch:
                mismatches.append(f'第{line_no(i)}行: {ch!r} 与最近开括号 {top_ch!r}(第{line_no(top_pos)}行) 类型不匹配')
    depth = len(stack)
    leftover = [f'第{line_no(p)}行: 未闭合的 {c!r}' for c, p in stack]
    ok = depth == 0 and not mismatches
    return {
        'status': 'PASS' if ok else 'FAIL',
        'summary': '结束深度 0，无不匹配' if ok else f'结束深度 {depth}，{len(mismatches)} 处不匹配',
        'details': mismatches + leftover,
    }


# ---------- 章节标记（用于定位裸色值白名单区） ----------

SECTION_BANNER_RE = re.compile(r'/\*\s*═{5,}\s*(\d+[a-z]?)\.\s+([^\n═]*?)\s*═{5,}')


def find_zones(original: str):
    """返回 [(start, end), ...] 白名单区间列表 + 未能定位的标记警告列表。"""
    warnings = []
    zones = []
    banners = [(m.start(), m.group(1)) for m in SECTION_BANNER_RE.finditer(original)]

    def zone_for_section(num: str, label: str):
        idx = next((i for i, (_, g1) in enumerate(banners) if g1 == num), None)
        if idx is None:
            warnings.append(f'找不到章节标记 "{num}."（{label}），该白名单区未生效，裸色值检查可能误报')
            return None
        start = banners[idx][0]
        end = banners[idx + 1][0] if idx + 1 < len(banners) else len(original)
        return (start, end)

    z = zone_for_section('0b', 'TOKEN 基座')
    if z:
        zones.append(z)

    syntax_marker = original.find('语法高亮调色板')
    if syntax_marker == -1:
        warnings.append('找不到"语法高亮调色板"标记，该白名单区未生效，裸色值检查可能误报')
    else:
        comment_close = original.find('*/', syntax_marker)
        search_from = comment_close + 2 if comment_close != -1 else syntax_marker
        m = re.search(r'/\*\s*-{2,}', original[search_from:])
        syntax_end = search_from + m.start() if m else len(original)
        zones.append((syntax_marker, syntax_end))

    z = zone_for_section('4', 'Callouts')
    if z:
        zones.append(z)

    return zones, warnings


def in_zones(pos: int, zones) -> bool:
    return any(s <= pos < e for s, e in zones)


# ---------- 检查 2：backdrop-filter 白名单 ----------

BACKDROP_RE = re.compile(r'(?:-webkit-)?backdrop-filter\s*:\s*([^;]+);')


def extract_backdrop_selectors(stripped: str) -> set[str]:
    selectors = set()
    for m in BACKDROP_RE.finditer(stripped):
        value = re.sub(r'!important', '', m.group(1), flags=re.I).strip()
        if value.lower() == 'none':
            continue
        sel = find_enclosing_selector(stripped, m.start())
        if sel:
            selectors.add(normalize_ws(sel))
    return selectors


def check_backdrop_whitelist(current: set[str], baseline: set[str]):
    new = sorted(current - baseline)
    removed = sorted(baseline - current)
    if new:
        return {'status': 'FAIL', 'summary': f'新增 {len(new)} 处未登记选择器', 'details': new}
    if removed:
        return {'status': 'WARN', 'summary': f'减少 {len(removed)} 处（建议 --update-baseline）', 'details': removed}
    return {'status': 'PASS', 'summary': f'{len(current)} 处，与基线一致', 'details': []}


# ---------- 检查 3：!important 台账 ----------

IMPORTANT_RE = re.compile(r'(-{0,2}[A-Za-z][A-Za-z0-9-]*)\s*:\s*[^;{}]+!important\s*;')


def selector_key(selector: str) -> str:
    first_line = selector.split('\n')[0]
    return normalize_ws(first_line)[:80]


def extract_important_ledger(stripped: str) -> set[str]:
    ledger = set()
    for m in IMPORTANT_RE.finditer(stripped):
        prop = m.group(1)
        sel = find_enclosing_selector(stripped, m.start())
        ledger.add(f'{selector_key(sel)}::{prop}')
    return ledger


def check_important_ledger(current: set[str], baseline: set[str]):
    new = sorted(current - baseline)
    removed = sorted(baseline - current)
    if new:
        return {'status': 'FAIL', 'summary': f'新增 {len(new)} 条未登记 !important', 'details': new}
    if removed:
        return {'status': 'WARN', 'summary': f'减少 {len(removed)} 条（建议 --update-baseline）', 'details': removed}
    return {'status': 'PASS', 'summary': f'{len(current)} 条，与基线一致', 'details': []}


# ---------- @settings 解析（供检查 4 / 检查 6 共用） ----------

SETTINGS_BLOCK_RE = re.compile(r'/\*\s*@settings\n(.*?)\n\*/', re.S)


def extract_settings_body(original: str):
    m = SETTINGS_BLOCK_RE.search(original)
    return m.group(1) if m else None


def parse_settings_items(settings_body: str):
    """手工解析 @settings YAML 列表：按独立 '-' 行分块，块内 key: value 逐行收集。"""
    chunks = re.split(r'(?m)^\s*-\s*$', settings_body)
    items = []
    for chunk in chunks[1:]:
        item = {}
        for line in chunk.splitlines():
            m = re.match(r'^\s*([A-Za-z0-9_-]+):\s*(.*)$', line)
            if m:
                item[m.group(1)] = m.group(2).strip()
        if item:
            items.append(item)
    return items


VARIABLE_TYPES_PREFIX = 'variable-'


# ---------- 检查 4：Token 孤儿 ----------

TOKEN_DECL_RE = re.compile(r'(--(?:jv|jadeveil)-[A-Za-z0-9-]+)\s*:')
TOKEN_VAR_REF_RE = re.compile(r'var\(\s*(--(?:jv|jadeveil)-[A-Za-z0-9-]+)')


def check_token_orphans(stripped: str, settings_items):
    defined = set(TOKEN_DECL_RE.findall(stripped))
    for item in settings_items:
        t = item.get('type', '')
        iid = item.get('id', '')
        if t.startswith(VARIABLE_TYPES_PREFIX) and iid:
            name = f'--{iid}'
            if TOKEN_PREFIX_RE.match(name):
                defined.add(name)
    referenced = set(TOKEN_VAR_REF_RE.findall(stripped))

    never_referenced = sorted(defined - referenced)
    never_defined = sorted(referenced - defined)

    if never_defined:
        status = 'FAIL'
    elif never_referenced:
        status = 'WARN'
    else:
        status = 'PASS'
    summary_parts = []
    if never_defined:
        summary_parts.append(f'{len(never_defined)} 个引用未定义')
    if never_referenced:
        summary_parts.append(f'{len(never_referenced)} 个定义未引用')
    summary = '，'.join(summary_parts) if summary_parts else f'{len(defined)} 个 token 全部有定义且被引用'
    details = [f'FAIL 引用未定义: {n}' for n in never_defined] + [f'WARN 定义未引用: {n}' for n in never_referenced]
    return {'status': status, 'summary': summary, 'details': details}


# ---------- 检查 5：裸色值告警 ----------

RAW_COLOR_RE = re.compile(r'#[0-9A-Fa-f]{3,8}\b|\b(?:rgba?|hsla?|oklch)\s*\(', re.IGNORECASE)


def extract_raw_colors(stripped: str, original: str, zones, line_no):
    locations = []
    for m in RAW_COLOR_RE.finditer(stripped):
        if in_zones(m.start(), zones):
            continue
        ln = line_no(m.start())
        line_start = original.rfind('\n', 0, m.start()) + 1
        line_end = original.find('\n', m.start())
        if line_end == -1:
            line_end = len(original)
        snippet = normalize_ws(original[line_start:line_end])[:100]
        locations.append(f'{ln}: {snippet}')
    return locations


def check_raw_colors(current_locations: list[str], baseline_count: int, baseline_locations: list[str]):
    current_count = len(current_locations)
    if current_count > baseline_count:
        new = sorted(set(current_locations) - set(baseline_locations))
        if not new:
            new = current_locations
        return {
            'status': 'WARN',
            'summary': f'{current_count} 处，较基线 {baseline_count} 处增加 {current_count - baseline_count}（列出新增行）',
            'details': new,
        }
    return {'status': 'PASS', 'summary': f'{current_count} 处（基线 {baseline_count} 处，未增加）', 'details': []}


# ---------- 检查 6：@settings YAML 结构 ----------

def check_settings_yaml(settings_body: str, settings_items):
    if settings_body is None:
        return {'status': 'FAIL', 'summary': '找不到 /* @settings ... */ 块', 'details': []}

    if HAS_YAML:
        try:
            parsed = yaml.safe_load(settings_body)
        except Exception as e:
            return {'status': 'FAIL', 'summary': f'YAML 解析失败: {e}', 'details': []}
        if not isinstance(parsed, dict) or not isinstance(parsed.get('settings'), list):
            return {'status': 'FAIL', 'summary': '顶层缺少 settings 列表', 'details': []}
        issues = []
        for i, item in enumerate(parsed['settings']):
            if not isinstance(item, dict):
                issues.append(f'第 {i} 项不是映射: {item!r}')
                continue
            iid = item.get('id', f'(index {i})')
            for field in ('id', 'title', 'type'):
                if field not in item:
                    issues.append(f'{iid}: 缺少 {field}')
            if item.get('type') == 'variable-number-slider':
                for field in ('default', 'min', 'max'):
                    if field not in item:
                        issues.append(f'{iid}: variable-number-slider 缺少 {field}')
        if issues:
            return {'status': 'FAIL', 'summary': f'{len(issues)} 处字段缺失（pyyaml 完整解析）', 'details': issues}
        return {'status': 'PASS', 'summary': f'{len(parsed["settings"])} 项设置，pyyaml 完整解析通过', 'details': []}

    # 降级：结构检查（缩进一致性 + 必备字段正则存在性）
    issues = []
    dash_indents = {len(m.group(1)) for m in re.finditer(r'(?m)^(\s*)-\s*$', settings_body)}
    if len(dash_indents) > 1:
        issues.append(f'"-" 列表项缩进不一致: {sorted(dash_indents)}')
    for i, item in enumerate(settings_items):
        iid = item.get('id', f'(index {i})')
        for field in ('id', 'title', 'type'):
            if field not in item:
                issues.append(f'{iid}: 缺少 {field}')
        if item.get('type') == 'variable-number-slider':
            for field in ('default', 'min', 'max'):
                if field not in item:
                    issues.append(f'{iid}: variable-number-slider 缺少 {field}')
    if issues:
        return {'status': 'FAIL', 'summary': f'{len(issues)} 处结构问题（降级检查，未完整解析 YAML）', 'details': issues}
    return {
        'status': 'WARN',
        'summary': f'{len(settings_items)} 项设置，结构检查通过——但无 pyyaml，未做完整 YAML 解析',
        'details': ['建议安装 pyyaml 以获得完整校验'],
    }


# ---------- 基线读写 ----------

def load_baseline():
    if not BASELINE_PATH.exists():
        return {
            'backdrop_filter_selectors': [],
            'important_ledger': [],
            'raw_color_count': 0,
            'raw_color_locations': [],
        }
    return json.loads(BASELINE_PATH.read_text())


def write_baseline(css_path: Path, backdrop_selectors, important_ledger, raw_color_locations):
    original = css_path.read_text()
    baseline = {
        'generated_at': datetime.datetime.now().isoformat(timespec='seconds'),
        'theme_css_lines': original.count('\n') + 1,
        'backdrop_filter_selectors': sorted(backdrop_selectors),
        'important_ledger': sorted(important_ledger),
        'raw_color_count': len(raw_color_locations),
        'raw_color_locations': raw_color_locations,
    }
    BASELINE_PATH.write_text(json.dumps(baseline, ensure_ascii=False, indent=2) + '\n')
    return baseline


# ---------- 主流程 ----------

def run_checks(css_path: Path):
    original = css_path.read_text()
    stripped = strip_comments_and_strings(original)
    line_no = make_line_finder(original)

    zones, zone_warnings = find_zones(original)
    settings_body = extract_settings_body(original)
    settings_items = parse_settings_items(settings_body) if settings_body else []

    backdrop_selectors = extract_backdrop_selectors(stripped)
    important_ledger = extract_important_ledger(stripped)
    raw_color_locations = extract_raw_colors(stripped, original, zones, line_no)

    return {
        'original': original,
        'stripped': stripped,
        'line_no': line_no,
        'zone_warnings': zone_warnings,
        'backdrop_selectors': backdrop_selectors,
        'important_ledger': important_ledger,
        'raw_color_locations': raw_color_locations,
        'settings_body': settings_body,
        'settings_items': settings_items,
    }


def print_check(idx: int, name: str, result: dict):
    icon = {'PASS': '✅', 'FAIL': '❌', 'WARN': '⚠️'}[result['status']]
    print(f'{icon} {idx}. {name}: {result["status"]} — {result["summary"]}')
    for d in result['details'][:50]:
        print(f'   - {d}')


def main():
    argv = sys.argv[1:]
    css_path = DEFAULT_THEME_CSS
    if '--file' in argv:
        i = argv.index('--file')
        css_path = Path(argv[i + 1])

    if not css_path.exists():
        print(f'FATAL: 文件不存在 {css_path}')
        sys.exit(2)

    data = run_checks(css_path)

    if '--update-baseline' in argv:
        baseline = write_baseline(css_path, data['backdrop_selectors'], data['important_ledger'], data['raw_color_locations'])
        print(f'# 基线已写入 {BASELINE_PATH}')
        print(f'- generated_at: {baseline["generated_at"]}')
        print(f'- theme_css_lines: {baseline["theme_css_lines"]}')
        print(f'- backdrop_filter_selectors: {len(baseline["backdrop_filter_selectors"])} 处')
        print(f'- important_ledger: {len(baseline["important_ledger"])} 条')
        print(f'- raw_color_count: {baseline["raw_color_count"]} 处')
        if data['zone_warnings']:
            print('- ⚠️  白名单区标记警告:')
            for w in data['zone_warnings']:
                print(f'  - {w}')
        return

    baseline = load_baseline()

    print('# Jadeveil Theme Lint Report')
    print(f'- file: {css_path} ({data["original"].count(chr(10)) + 1} 行)')
    baseline_info = f'{baseline.get("generated_at", "?")} (对应 {baseline.get("theme_css_lines", "?")} 行)' if BASELINE_PATH.exists() else '不存在（先跑 --update-baseline）'
    print(f'- baseline: {baseline_info}')
    for w in data['zone_warnings']:
        print(f'- ⚠️  {w}')
    print()

    results = []
    results.append(check_bracket_balance(data['stripped'], data['line_no']))
    results.append(check_backdrop_whitelist(data['backdrop_selectors'], set(baseline.get('backdrop_filter_selectors', []))))
    results.append(check_important_ledger(data['important_ledger'], set(baseline.get('important_ledger', []))))
    results.append(check_token_orphans(data['stripped'], data['settings_items']))
    results.append(check_raw_colors(data['raw_color_locations'], baseline.get('raw_color_count', 0), baseline.get('raw_color_locations', [])))
    results.append(check_settings_yaml(data['settings_body'], data['settings_items']))

    names = ['括号配平', 'backdrop-filter 白名单', '!important 台账', 'Token 孤儿', '裸色值告警', '@settings YAML']
    for i, (name, result) in enumerate(zip(names, results), start=1):
        print_check(i, name, result)

    n_fail = sum(1 for r in results if r['status'] == 'FAIL')
    n_warn = sum(1 for r in results if r['status'] == 'WARN')
    n_pass = sum(1 for r in results if r['status'] == 'PASS')
    print(f'\nTOTAL: {n_fail} FAIL, {n_warn} WARN, {n_pass} PASS')
    sys.exit(1 if n_fail else 0)


if __name__ == '__main__':
    main()
