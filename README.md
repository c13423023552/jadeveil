# Jadeveil 玉幕

English | [中文](README.zh.md)

An Obsidian theme for CJK-heavy technical writing: translucent sidebars, a warm paper-toned reading surface, and a low-saturation jade green accent.

| Light | Dark |
|---|---|
| ![light](screenshot-light.png) | ![dark](screenshot-dark.png) |

## Features

- Translucent sidebars / ribbon / status bar (`backdrop-filter`), with density sliders for light and dark mode
- Warm ivory reading background in light mode; graphite tones in dark mode
- One accent hue drives checkboxes, tags, selection, focus rings, and callouts — adjustable via a hue slider, kept low-saturation so it never turns neon
- CJK typography details: `text-autospace` / `text-spacing-trim` where supported, CJK-friendly line heights, ligatures disabled in code blocks
- Contrast checked in both modes (body text ~13:1 light / ~11:1 dark; links, syntax colors, callout titles meet WCAG AA)
- Extra task states: `[-]` cancelled, `[>]` forwarded, `[?]` question
- Sensible fallbacks: `@media print` resets to black-on-white; without window translucency the theme just renders solid

## Requirements

- The glass effect needs **Settings → Appearance → Translucent window** (macOS). Without it the theme falls back to solid surfaces; there is also a toggle to disable all blur.
- Fonts are system stacks only (SF Pro / PingFang, falling back to Hiragino / Microsoft YaHei). Nothing is loaded from the network.
- [Style Settings](https://github.com/mgmeyers/obsidian-style-settings) is optional — it exposes the sliders and toggles (glass density, accent hue, font size/line width, OLED black, code line numbers, etc.). Defaults work without it.

## Install

Until it's in the community theme store: copy `manifest.json` and `theme.css` into `<vault>/.obsidian/themes/Jadeveil/`, then pick **Jadeveil** in *Settings → Appearance → Themes*.

## Notes

`theme.css` is heavily commented (in Chinese) — token structure, why each `!important` exists, and which Obsidian internals the theme depends on. [`DESIGN.md`](DESIGN.md) has a short summary of the design decisions.

## License

[MIT](LICENSE)
