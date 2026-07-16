#!/usr/bin/env bash
# build_app.sh — 构建 FlashESP32.app（兼容 Intel + Apple Silicon Mac）
#
# 用法：
#   cd flashesp32 && bash build_app.sh
#
# 产物：
#   flashesp32/dist/FlashESP32.app
#
# 若系统 Python 不含 x86_64 架构，请先从 python.org 安装 Universal2 Python：
#   https://www.python.org/downloads/macos/
# 或手动指定：
#   PYTHON_BIN=/Library/Frameworks/.../python3 bash build_app.sh
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_NAME="FlashESP32"
DIST_DIR="$SCRIPT_DIR/dist"
TOOLCHAIN_DIR="$SCRIPT_DIR/.toolchain"

# 内置 Universal2 Python 版本（含 Intel x86_64 + Apple Silicon arm64 两个架构切片）
PY_UNIVERSAL_VER="${PY_UNIVERSAL_VER:-3.12.8}"
PY_UNIVERSAL_SHORT="3.12"

cd "$SCRIPT_DIR"

# ── 查找含 Intel x86_64 架构的 Python ─────────────────────────────────────────
python_archs() {
  local executable real
  executable="$1"
  real="$("$executable" -c 'import os, sys; print(os.path.realpath(sys.executable))' 2>/dev/null)" || return 1
  lipo -archs "$real" 2>/dev/null | xargs
}

# ── 自动下载并本地解压 Universal2 Python（无需 sudo，全部内置于 .toolchain）──
provision_universal_python() {
  local ver="$PY_UNIVERSAL_VER" short="$PY_UNIVERSAL_SHORT"
  local fwbin="$TOOLCHAIN_DIR/Python.framework/Versions/$short/bin/python3"

  # 已解压且可用则直接复用
  if [[ -x "$fwbin" ]] && python_archs "$fwbin" 2>/dev/null | grep -q "x86_64"; then
    echo "$fwbin"
    return 0
  fi

  mkdir -p "$TOOLCHAIN_DIR"
  local pkg="$TOOLCHAIN_DIR/python-universal2-$ver.pkg"

  if [[ ! -s "$pkg" ]]; then
    echo "→  未找到 Universal2 Python，正在自动下载并内置到 .toolchain ..." >&2
    local urls=(
      "https://www.python.org/ftp/python/$ver/python-$ver-macos11.pkg"
      "https://mirrors.aliyun.com/python-release/macos/python-$ver-macos11.pkg"
      "https://registry.npmmirror.com/-/binary/python/$ver/python-$ver-macos11.pkg"
      "https://mirrors.huaweicloud.com/python/$ver/python-$ver-macos11.pkg"
    )
    local ok=""
    for u in "${urls[@]}"; do
      echo "   ↓  $u" >&2
      if curl -fL --retry 2 --connect-timeout 20 --max-time 1800 "$u" -o "$pkg" \
         && [[ -s "$pkg" ]]; then
        ok=1
        break
      fi
      rm -f "$pkg"
    done
    if [[ -z "$ok" ]]; then
      echo "❌  Universal2 Python 下载失败（网络不可达）。" >&2
      echo "    可手动下载后放到：$pkg" >&2
      echo "    下载页：https://www.python.org/downloads/macos/" >&2
      return 1
    fi
  fi

  echo "→  解压 Universal2 Python 框架（本地，无需管理员权限）..." >&2
  local exp="$TOOLCHAIN_DIR/expand"
  rm -rf "$exp" "$TOOLCHAIN_DIR/Python.framework"
  pkgutil --expand-full "$pkg" "$exp" >/dev/null

  # 从 Python_Framework 组件 payload 中提取 Python.framework
  local fwsrc
  fwsrc="$(find "$exp" -type d -name "Python.framework" -path "*Payload*" 2>/dev/null | head -1)"
  if [[ -z "$fwsrc" ]]; then
    fwsrc="$(find "$exp" -type d -name "Python.framework" 2>/dev/null | head -1)"
  fi
  if [[ -z "$fwsrc" ]]; then
    echo "❌  未能在安装包中定位 Python.framework。" >&2
    return 1
  fi

  cp -R "$fwsrc" "$TOOLCHAIN_DIR/Python.framework"
  rm -rf "$exp"

  if [[ ! -x "$fwbin" ]]; then
    # 某些版本仅提供 python3.x，补建 python3 符号链接
    local alt="$TOOLCHAIN_DIR/Python.framework/Versions/$short/bin/python$short"
    if [[ -x "$alt" ]]; then
      ln -sf "python$short" "$fwbin"
    fi
  fi

  if [[ -x "$fwbin" ]] && python_archs "$fwbin" 2>/dev/null | grep -q "x86_64"; then
    echo "$fwbin"
    return 0
  fi
  echo "❌  解压后的 Python 不含 x86_64 架构。" >&2
  return 1
}

PYTHON_BIN="${PYTHON_BIN:-}"

if [[ -n "$PYTHON_BIN" && ! -x "$PYTHON_BIN" ]]; then
  echo "❌  PYTHON_BIN 不可执行：$PYTHON_BIN"
  exit 1
fi

if [[ -z "$PYTHON_BIN" ]]; then
  CANDIDATES=()

  # 0a) 已内置到本地 .toolchain 的 x86_64 便携 Python（最优先，无需联网）
  while IFS= read -r candidate; do
    CANDIDATES+=("$candidate")
  done < <(find "$TOOLCHAIN_DIR/python-x86_64/bin" -maxdepth 1 \
           \( -name 'python3.*' -o -name 'python3' \) -type f 2>/dev/null | sort -Vr)

  # 0b) 之前已内置到 .toolchain 的 Universal2 Python（保证 Intel 兼容）
  [[ -x "$TOOLCHAIN_DIR/Python.framework/Versions/$PY_UNIVERSAL_SHORT/bin/python3" ]] \
    && CANDIDATES+=("$TOOLCHAIN_DIR/Python.framework/Versions/$PY_UNIVERSAL_SHORT/bin/python3")

  # 1) python.org Framework Python（最可能含 universal2）
  while IFS= read -r candidate; do
    CANDIDATES+=("$candidate")
  done < <(find /Library/Frameworks/Python.framework/Versions -path '*/bin/python3' \
           -type f 2>/dev/null | sort -Vr)

  # 2) Homebrew x86_64 前缀
  [[ -x "/usr/local/bin/python3" ]] && CANDIDATES+=("/usr/local/bin/python3")

  # 3) 系统 PATH
  command -v python3 &>/dev/null && CANDIDATES+=("$(command -v python3)")

  for candidate in "${CANDIDATES[@]}"; do
    if python_archs "$candidate" 2>/dev/null | grep -q "x86_64"; then
      PYTHON_BIN="$candidate"
      break
    fi
  done
fi

# 未找到含 x86_64 的 Python时，仅在明确允许联网后下载 Universal2 Python。
if [[ -z "$PYTHON_BIN" ]]; then
  if [[ "${ALLOW_NETWORK:-0}" == "1" ]]; then
    echo "ℹ️   系统未找到含 Intel x86_64 的 Python，下载 Universal2 Python。"
    if PROVISIONED="$(provision_universal_python)"; then
      PYTHON_BIN="$PROVISIONED"
      echo "✔  已内置 Universal2 Python：$PYTHON_BIN"
    fi
  else
    echo "❌  未找到含 Intel x86_64 的 Python，默认离线模式不会自动下载。"
    echo "    请恢复 flashesp32/.toolchain/python-x86_64，或明确允许联网："
    echo "    ALLOW_NETWORK=1 ./build_app.sh"
    exit 1
  fi
fi

if [[ -z "$PYTHON_BIN" ]]; then
  echo "❌  无法获得含 Intel x86_64 架构的 Python。"
  echo "    请连网后重试，或从 python.org 手动安装 Universal2 Python 3.9+："
  echo "    https://www.python.org/downloads/macos/"
  echo ""
  echo "    安装后重新执行：bash build_app.sh"
  exit 1
fi

# ── Python 版本检查 ───────────────────────────────────────────────────────────
PY_VER=$("$PYTHON_BIN" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
if ! "$PYTHON_BIN" -c 'import sys; raise SystemExit(sys.version_info < (3, 9))'; then
  echo "❌  Python $PY_VER 版本过低，需要 3.9+"
  exit 1
fi
echo "✔  Python ${PY_VER}：$PYTHON_BIN"

# ── tkinter 检查 ──────────────────────────────────────────────────────────────
if ! "$PYTHON_BIN" -c "import tkinter" &>/dev/null; then
  echo "❌  所选 Python 未内置 tkinter。"
  echo "    请改用 python.org 安装包（内含 tkinter）。"
  exit 1
fi

# ── 虚拟环境 ──────────────────────────────────────────────────────────────────
if [[ -x "venv/bin/python" ]] && ! python_archs "venv/bin/python" 2>/dev/null | grep -q "x86_64"; then
  echo "→  删除架构不兼容的旧虚拟环境 ..."
  rm -rf venv
fi
if [[ ! -d "venv" ]]; then
  echo "→  创建虚拟环境 ..."
  "$PYTHON_BIN" -m venv venv
fi
# shellcheck disable=SC1091
source venv/bin/activate

# ── 确认 venv Python 含 x86_64 ───────────────────────────────────────────────
PY_REAL=$(python -c "import os, sys; print(os.path.realpath(sys.executable))")
PY_ARCHS=$(lipo -archs "$PY_REAL" 2>/dev/null | xargs || echo "$(uname -m)")

if echo "$PY_ARCHS" | grep -q "x86_64" && echo "$PY_ARCHS" | grep -q "arm64"; then
  TARGET_ARCH="universal2"
  echo "✔  Universal2 Python — 将构建通用 app（Intel + Apple Silicon）"
elif echo "$PY_ARCHS" | grep -q "x86_64"; then
  TARGET_ARCH="x86_64"
  echo "✔  Intel Python — 将构建 x86_64 app（可通过 Rosetta 在 Apple Silicon 运行）"
else
  echo "❌  当前 Python 仅含 arm64，无法生成 Intel Mac 兼容 app。"
  echo "    请从 python.org 安装 Universal2 Python 后重新执行：bash build_app.sh"
  exit 1
fi

# macOS Catalina (10.15) 是支持 Python 3.9 的最低版本
DEPLOY_TARGET="10.15"
export MACOSX_DEPLOYMENT_TARGET="$DEPLOY_TARGET"
echo "✔  最低 macOS 部署版本：${DEPLOY_TARGET}（Catalina+）"

# ── 检查构建依赖 ──────────────────────────────────────────────────────────────
# venv 随 flashesp32 一起分发；依赖完整时直接离线构建，不访问任何 pip 索引。
if python - <<'PY'
import importlib.metadata
import PyInstaller
import cryptography
import esptool

if int(importlib.metadata.version("cryptography").split(".", 1)[0]) >= 49:
    raise SystemExit(1)
PY
then
  echo "✔  使用内置构建依赖（离线，不访问网络）"
else
  if [[ "${ALLOW_NETWORK:-0}" != "1" ]]; then
    echo "❌  内置构建依赖不完整。为避免网络延迟，默认不会访问 pip 索引。"
    echo "    请恢复 flashesp32/venv，或明确允许联网后重试："
    echo "    ALLOW_NETWORK=1 ./build_app.sh"
    exit 1
  fi
  echo "→  已允许联网，使用 pip 安装 esptool + pyinstaller ..."
  echo "    可通过 PIP_INDEX_URL 自行指定镜像；未指定时使用 pip 默认索引。"
  export PIP_DISABLE_PIP_VERSION_CHECK=1
  # cryptography<49 避免在旧 macOS 上的 ABI 兼容问题
  python -m pip install "cryptography<49" esptool pyinstaller
fi

# ── PyInstaller 打包 ──────────────────────────────────────────────────────────
# 清除上次 spec，确保参数生效
rm -f "${APP_NAME}.spec"
rm -rf "$DIST_DIR/$APP_NAME.app"

echo ""
echo "→  开始打包（PyInstaller，arch=${TARGET_ARCH}）..."
pyinstaller \
  --name                   "$APP_NAME" \
  --windowed \
  --noconfirm \
  --clean \
  --collect-all            esptool \
  --hidden-import          esptool \
  --target-arch            "$TARGET_ARCH" \
  --osx-bundle-identifier  com.esptools.flashesp32 \
  "$SCRIPT_DIR/main.py" 2>&1 | grep -v "^$"

APP_PATH="$DIST_DIR/$APP_NAME.app"

if [[ ! -d "$APP_PATH" ]]; then
  echo "❌  构建失败：未生成 $APP_PATH"
  exit 1
fi

# ── 架构验证 ──────────────────────────────────────────────────────────────────
APP_ARCHS=$(lipo -archs "$APP_PATH/Contents/MacOS/$APP_NAME" 2>/dev/null | xargs || true)
if ! echo "$APP_ARCHS" | grep -q "x86_64"; then
  echo "❌  验证失败：主程序架构为「${APP_ARCHS:-unknown}」，不含 Intel x86_64"
  exit 1
fi
echo "✔  App 架构验证通过：${APP_ARCHS}"

# ── Ad-hoc 签名（无需开发者账号，解除 Gatekeeper 直接运行限制）────────────────
echo "→  Ad-hoc 代码签名 ..."
codesign --force --deep --sign - "$APP_PATH" 2>/dev/null \
  && echo "  ✔  签名完成" \
  || echo "  ⚠  签名跳过（不影响使用）"

# ── 完成 ──────────────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  ✅  构建完成                                                  ║"
echo "╠══════════════════════════════════════════════════════════════╣"
printf "║  App    : %-51s ║\n" "$APP_PATH"
printf "║  架构   : %-51s ║\n" "$APP_ARCHS"
printf "║  最低   : macOS %-44s ║\n" "${DEPLOY_TARGET}+"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║  分发给其他 Mac 时，将 dist/FlashESP32.app 整个文件夹打包发送 ║"
echo "╚══════════════════════════════════════════════════════════════╝"

if [[ -t 0 ]]; then
  read -r -p "立即打开 App？[y/N] " OPEN_NOW
  if [[ "$OPEN_NOW" =~ ^[Yy]$ ]]; then
    open "$APP_PATH"
  fi
fi
