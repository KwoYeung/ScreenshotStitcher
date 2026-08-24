# 截图自动拼接

本地桌面工具，支持 macOS 和 Windows。可以固定截取同一屏幕区域，再根据视觉重叠将自由平移的大画布或纵向长页面还原成完整图片。

Developed by **KwoYeung**.

## 功能

- 任意显示器设置固定取景框，支持左右或上下排列的多屏桌面。
- macOS 默认全局快捷键 `Control+Option+S`；Windows 默认 `Ctrl+Alt+S`，也可切换为其他组合。
- 可上下、左右、斜向移动底层画布，使用二维特征匹配自动定位。
- 临时截图使用 `月日-时分-序号` 名称，例如 `0824-1437-001.png`。
- 截图管理页提供缩略图、多选、单图预览、移除选中和全部清理。
- 拼接完成后自动进入结果页，集中显示预览、匹配记录、警告和保存按钮。
- 保留传统纵向长图模式；只有关闭自动排序时才显示手动顺序调整。

## 安装

需要 Python 3.10 或更高版本。

### macOS

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python app.py
```

首次截图时，macOS 可能要求屏幕录制权限。请在“系统设置 → 隐私与安全性 → 屏幕与系统音频录制”中允许 Python 或 VS Code，然后重新启动程序。

### Windows 10/11

在 PowerShell 中执行：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python app.py
```

如果 PowerShell 禁止激活脚本，可以不激活环境，直接执行：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

程序会启用 Windows 每显示器 DPI 感知，避免 125%/150% 缩放下固定框与截图像素错位。Windows 区域截图由 Pillow 完成，不调用 macOS 命令。

## 使用流程

1. 在“截图管理”页点击“设置固定截图区域”，拖动选择一次。
2. 蓝色固定框和顶部中央的截图按钮会持续显示。
3. 最小化主窗口，在固定框内移动底层大图。
4. 使用全局快捷键截图，无需先切换回本程序。
5. 每次截图后，悬浮按钮会显示“第 N 张截图成功”。建议相邻截图保留 25%～40% 重叠。
6. 回到“截图管理”页，通过缩略图和单图预览检查内容，必要时移除错误截图。
7. 选择“自由平移画布”并点击“开始二维拼接”。
8. 程序自动进入“拼接结果”页；检查匹配记录后保存 PNG/JPEG/WebP。

点击悬浮区的“🏠 主菜单”可以恢复主窗口。如果默认快捷键被其他软件占用，可在截图管理页切换为备用组合。

## 缓存规则

- 每次启动使用独立的系统临时目录。
- 移除程序截取的图片时，对应缓存立即删除。
- “全部清理”会删除临时截图，清空拼接结果和匹配记录；用户导入的原图只会移出列表，不会删除原文件。
- 拼接结果成功保存后，本次临时截图全部删除并从列表移除。
- 保存失败时保留缓存，防止截图丢失。
- 正常关闭或终端中断时会尝试清理本次缓存目录。

## 拼接模式

- **自由平移画布**：允许 X/Y 任意位移。每张新截图会尝试连接到任意已有截图，支持横向、纵向、斜向和蛇形采集。
- **纵向长图**：支持固定导航栏、滚动条忽略、自动排序和纵向裁切。关闭自动排序后可以手动调整图片顺序。

匹配失败时程序优先保全内容，不进行猜测性删除。

## 在 VS Code 中运行

项目包含 `.vscode/settings.json` 和调试配置。请用 VS Code 打开整个项目文件夹，选择项目内的 `.venv` 解释器，然后按 `F5` 运行“运行截图拼接工具”。

## 测试

```bash
python -m unittest discover -s tests -v
```

当前自动测试覆盖纵向匹配、二维平移、蛇形采集、匹配失败保全、框线范围、多屏负坐标和快捷键映射。

## 打包发布

项目提供 macOS DMG 和 Windows EXE 打包配置，发布包会携带 Python 及所有图片依赖，终端用户无需配置 Python 环境。详见 [packaging/README.md](packaging/README.md)。
