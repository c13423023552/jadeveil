# Jadeveil 3.0 视觉宪法

## 品牌定义

Jadeveil 是面向中文技术写作的 Obsidian 主题。核心隐喻是“玉幕承载纸墨”：
导航与控件像半透明玉幕悬浮于内容之上，正文保持稳定、安静和高可读。

**v3 温润如玉三分法**：温从纸来（浅色正文为暖象牙纸 #FBFAF7，非冷白）、
润从玻璃来（chrome 玻璃真正半透明，环境色透入体内）、清透从 alpha 来
（tint alpha 走 Style Settings「玉幕浓度」滑杆，默认浅 0.64 / 深 0.85，
saturate 1.55/1.7）。accent 为青玉（色相 166、饱和度 46%），checkbox /
tag / 选区 / callout 全族「玉化」（低饱和），滑杆扫过任意色相不出霓虹。

**两条硬法则（多轮用户反馈收敛）**：
1. 深浅玻璃公式不对称——浅色清透靠低 alpha（白 tint 安全），深色清透
   靠 blur 透光影 + 高 alpha（低 alpha 深 tint 在暗桌面塌成黑框）。
2. 玻璃+纸双材质体系里给组件造可见性，用海拔（描边/投影/浮起），
   禁止引入第三种底色——顶部页签「透明→灰带→浮起胶囊」三轮迭代的教训。

## 材质层级

1. **Liquid Glass 功能层**：侧栏、标签、导航控件、菜单、命令面板和弹窗。
2. **Standard Material 内容层**：正文、代码块、表格、Callout、图片和 Mermaid。
3. **Opaque fallback**：关闭透明、全屏、降低透明度或 `jadeveil-glass-off` 时使用。

禁止在正文内容块上使用 backdrop-filter。玻璃用于区分交互层与内容层，不作为装饰。

## 光学规则

- 全局假设光源位于左上方。
- 上边缘和左边缘使用窄镜面高光，右侧和底部使用短距离环境影。
- 大面积侧栏只显示环境透光和边缘光；交互控件才显示明确 sheen。
- 深色模式降低高光面积而不是简单降低全部 alpha。
- hover 不改变字号、字重、尺寸和位置。

## 玻璃厚度

| 层级 | 使用位置 | 特征 |
|---|---|---|
| Thin | hover、活动标签、分段控件 | 实心哑光小 pill（分段控件 active 为 surface-float），短影、清晰边缘 |
| Regular | 左右侧栏、Ribbon（含红绿灯拖拽区）、状态栏 | tint alpha 浅 0.64 / 深 0.85、24px blur、saturate 1.55，文字可读；Ribbon 与侧栏同玻璃融为一块玉幕 |
| Thick | 命令面板、菜单、Modal | tint alpha 浅 0.76 / 深 0.72、28px blur、saturate 1.7、环境阴影 |

深色模式的玻璃边缘光（specular / border / sheen）带玉色
`rgba(214,240,228,…)`——夜里的玉在轮廓处呼吸，而非铺面。

## 内容规则

- 正文是唯一主舞台，不做玻璃卡片。
- 代码块占满正文 measure，使用 8px 圆角和单层轮廓。
- 表格、Callout、Metadata 优先使用色差和排版，不堆叠高光与阴影。
- H5/H6 不小于正文，通过字重、颜色和间距区分。

## API

- 公共 Style Settings 变量：`--jadeveil-*`
- 内部设计令牌：`--jv-*`
- body class：`jadeveil-*`
- Style Settings ID：`jadeveil`

## 验证矩阵

- 浅色 / 深色
- translucent on / off
- `jadeveil-glass-off` on / off
- 编辑态 / 阅读态
- 左右侧栏打开 / 关闭
- 命令面板、菜单和 hover 控件
- `prefers-reduced-motion`

前身 White Jade 时期的历史校准档案已随 v3 自持化清退（需要时查 git 历史）。
