#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$PROJECT_ROOT/.venv/bin/python}"
BUILD_DIR="$PROJECT_ROOT/build/macos"
DIST_DIR="$PROJECT_ROOT/dist/macos"
RELEASE_DIR="$PROJECT_ROOT/release/macos"
APP_NAME="截图自动拼接.app"
ARCH="$(uname -m)"
DMG_PATH="$RELEASE_DIR/截图自动拼接-macOS-$ARCH.dmg"

if [ ! -x "$PYTHON_BIN" ]; then
  echo "未找到构建用 Python：$PYTHON_BIN"
  echo "请先在项目目录创建 .venv 并安装 requirements.txt。"
  exit 1
fi

mkdir -p "$BUILD_DIR" "$DIST_DIR" "$RELEASE_DIR"
rm -rf "$BUILD_DIR" "$DIST_DIR" "$DMG_PATH"

"$PYTHON_BIN" -m PyInstaller \
  --noconfirm \
  --clean \
  --workpath "$BUILD_DIR" \
  --distpath "$DIST_DIR" \
  "$PROJECT_ROOT/packaging/macos.spec"

codesign --force --deep --sign - "$DIST_DIR/$APP_NAME"

STAGING_DIR="$(mktemp -d)"
trap 'rm -rf "$STAGING_DIR"' EXIT
cp -R "$DIST_DIR/$APP_NAME" "$STAGING_DIR/$APP_NAME"
ln -s /Applications "$STAGING_DIR/Applications"

hdiutil create \
  -volname "截图自动拼接" \
  -srcfolder "$STAGING_DIR" \
  -ov \
  -format UDZO \
  "$DMG_PATH"

echo
echo "macOS 安装包已生成："
echo "$DMG_PATH"
