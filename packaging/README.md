# 发布打包

源码文件不会被打包脚本修改或删除。中间文件位于 `build/` 和 `dist/`，可交付文件位于 `release/`。

## macOS DMG

在 macOS 项目根目录运行：

```bash
.venv/bin/python -m pip install -r requirements-build.txt
bash packaging/build_macos.sh
```

产物位于 `release/macos/`。构建出的应用架构与当前 Python 架构一致：Apple Silicon 环境生成 ARM64 版，Intel 环境生成 x86_64 版。

当前脚本使用 ad-hoc 签名，适合内部测试。对外公开分发前，建议改用 Apple Developer ID 签名并进行 notarization，否则其他 Mac 可能需要右键点击应用后选择“打开”。

## Windows EXE

将完整项目复制到 Windows 10/11，安装 64 位 Python 3.10 或更高版本，然后双击：

```text
packaging\build_windows.bat
```

脚本会创建独立的 `.venv-build-windows` 构建环境，产物为 `release\windows\ScreenshotStitcher.exe`。exe 中包含 Python、OpenCV、NumPy 和 Pillow，最终用户无需安装 Python。

PyInstaller 不支持从 macOS 交叉编译 Windows exe，因此 Windows 产物必须在 Windows 上构建和验证。

## Windows 附件补同步到 Gitee

如果 macOS DMG 已经先发布，Windows 打包完成后只需把 EXE 上传到同一个 GitHub Release，然后：

1. 打开 GitHub 仓库的 **Actions** 页面。
2. 选择 **Sync Release to Gitee**。
3. 点击 **Run workflow**；标签留空时默认同步最新 Release。

工作流会重新读取 GitHub Release 的所有附件，并在 Gitee 的同一版本中补充或替换同名文件，无需重新创建 Release。
