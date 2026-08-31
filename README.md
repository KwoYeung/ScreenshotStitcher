# ScreenshotStitcher 截图自动拼接

ScreenshotStitcher 是一款本地运行的桌面截图拼接工具。它可以自动识别多张截图的重叠区域，去除重复内容，并还原成完整图片。

适用于纵向长页面、可自由平移的大画布、地图以及其他无法一次完整截取的内容。图片分析和拼接均在本地完成。

**开发者：KwoYeung**

## 下载

**[前往 Releases 下载最新版](https://github.com/KwoYeung/ScreenshotStitcher/releases/latest)**

| 系统 | 安装包 | 说明 |
| --- | --- | --- |
| macOS | [`ScreenshotStitcher-v1.1.1-macOS-arm64.dmg`](https://github.com/KwoYeung/ScreenshotStitcher/releases/download/v1.1.1/ScreenshotStitcher-v1.1.1-macOS-arm64.dmg) | 适用于 Apple Silicon（M 系列芯片） |
| Windows | [`ScreenshotStitcher-v1.1.1-Windows-x64.exe`](https://github.com/KwoYeung/ScreenshotStitcher/releases/tag/v1.1.1) | 适用于 Windows 10/11，EXE 将补充到同一 Release |

安装包已包含程序所需的运行环境，**普通用户无需安装 Python、OpenCV 或其他依赖**。

## 主要功能

- 自动识别相邻截图的重叠区域，去除重复内容。
- 支持纵向长图以及上下、左右、斜向和蛇形移动的自由画布。
- 自由平移模式支持宽高不同的导入图片，无需人工裁成统一尺寸。
- 自动检测相邻图片的画布/内容缩放比变化，避免强行拼接造成变形。
- 默认“自动容错”会按内容分析图片关系，忽略文件名和导入顺序，并跳过少量比例异常或无法连接的图片。
- 可切换“严格顺序”用于排查问题，遇到第一个匹配失败即停止。
- 可设置固定截图区域，取景框会在移动底层画布时持续显示。
- 支持全局快捷键和置顶悬浮截图按钮。
- 支持多显示器和 Windows 缩放比。
- 左下角显示当前版本，并在后台通过 Gitee（GitHub 备用）检查新版本；断网时不影响使用。
- macOS 新增截图权限预检和一键修复，可清理旧版本留下的失效授权并引导重新授权。
- 提供截图缩略图、单图预览、匹配置信度和拼接结果预览。
- 拼接结果支持鼠标滚轮缩放和滚动条平移查看。
- 可导出 PNG、JPEG 和 WebP。
- 自动管理临时截图和拼接缓存。

## 安装

### macOS

1. 下载 `ScreenshotStitcher-v1.1.1-macOS-arm64.dmg`。
2. 打开 DMG，将应用拖入“应用程序”文件夹。
3. 首次启动时，按系统提示允许屏幕录制权限。

如果 macOS 阻止打开，请在“系统设置 → 隐私与安全性”中选择“仍要打开”。截图功能需要在“屏幕与系统音频录制”中授予权限，授权后请重新启动程序。

### Windows

1. 下载 `ScreenshotStitcher-v1.1.1-Windows-x64.exe`。
2. 双击运行，无需安装。

如果 Windows SmartScreen 在首次启动时显示提示，请核对下载来源为本项目的 GitHub Releases，然后选择“更多信息 → 仍要运行”。

## 使用方法

1. 打开程序，在“截图管理”页设置固定截图区域。
2. 最小化主窗口，在固定框内移动底层页面或画布。
3. 使用全局快捷键或悬浮“📷 截图”按钮连续截取图片。
4. 每次移动时建议保留 **25%～40%** 的相邻内容重叠。
5. 通过缩略图检查截图，必要时移除不合适的图片。
6. 根据内容选择“自由平移画布”或“纵向长图”，然后开始拼接。批量导入建议使用“自动容错”；需要逐张检查时可切换“严格顺序”。
7. 在结果页查看预览和匹配置信度，使用鼠标滚轮缩放，确认后保存。

默认全局快捷键：

- macOS：`Control + Option + S`
- Windows：`Ctrl + Alt + S`

如果快捷键与其他软件冲突，可在程序中切换其他组合。

## 使用建议

- 相邻截图应保留足够的共同内容，但不要完全重复。
- 导入图片可以使用不同宽高，但建议保持内容缩放比大致一致。
- 如预览中出现未对齐，请优先核实画布/内容缩放比；少量缩放变化可能因特征不足而无法稳定估算。
- 预览发现缺漏时，可直接返回截图管理补充、移除或替换图片后再次拼接，无需清空列表。
- 重复文字、固定导航栏或大面积纯色区域可能降低匹配置信度。
- 匹配失败时，程序会优先保留内容，避免猜测性删除。

## 隐私与缓存

- 截图和拼接均在本地完成，程序不会主动上传图片。
- 移除程序截取的图片时，对应临时文件会同步删除。
- “全部清理”会同时清理截图、拼接结果和匹配记录。
- 正常关闭程序时会尝试清理本次使用的临时目录。

## 问题反馈

如果遇到无法启动、截图错位或拼接失败，欢迎在 [Issues](https://github.com/KwoYeung/ScreenshotStitcher/issues) 中反馈。建议附上：

- 操作系统及版本。
- 显示器数量和缩放比。
- 问题现象和复现步骤。
- 在不包含隐私内容的前提下，提供示例截图。

## 开源许可

本项目基于 [MIT License](LICENSE) 开源。你可以自由使用、修改和分发本项目，但需保留原始版权和许可声明。

Copyright © 2026 KwoYeung.

<details>
<summary><strong>从源码运行与开发</strong></summary>

### 环境要求

- Python 3.10 或更高版本
- OpenCV
- NumPy
- Pillow

### 运行

```bash
python3 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
python -m pip install -r requirements.txt
python app.py
```

### 测试

```bash
python -m unittest discover -s tests -v
```

### 打包

项目中提供 macOS 和 Windows 打包配置，详见 [`packaging/README.md`](packaging/README.md)。

</details>
