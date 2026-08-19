#!/bin/bash
# macOS 打包脚本：venv → 安装依赖 → PyInstaller → dist/VConv.app → zip → 冒烟测试
# 用法: bash packaging/build_macos.sh
set -euo pipefail
cd "$(dirname "$0")/.."

PY=${PYTHON:-python3}
if [ ! -d .venv-build ]; then
  "$PY" -m venv .venv-build
fi
# shellcheck disable=SC1091
source .venv-build/bin/activate

python -m pip install --upgrade pip >/dev/null
python -m pip install -r requirements-dev.txt

rm -rf build dist
pyinstaller --noconfirm packaging/vconv.spec

VERSION=$(python -c "from vconv import __version__; print(__version__)")
ARCH=$(uname -m)
OUT="VConv-macos-${ARCH}-v${VERSION}.zip"
rm -f "$OUT"
cd dist && zip -rq "../$OUT" VConv.app && cd ..

# ---- 冒烟：产物能启动、首页与 API 可达 ----
SMOKE_PORT=18756
./dist/VConv.app/Contents/MacOS/VConv --port "$SMOKE_PORT" --no-browser >/tmp/vconv_smoke_app.log 2>&1 &
APP_PID=$!
trap 'kill $APP_PID 2>/dev/null || true' EXIT
for _ in $(seq 1 30); do
  if curl -sf "http://127.0.0.1:${SMOKE_PORT}/api/ffmpeg" >/dev/null 2>&1; then
    echo "冒烟通过: 打包产物 HTTP 服务正常"
    kill $APP_PID 2>/dev/null || true
    wait $APP_PID 2>/dev/null || true
    echo "产物: $OUT"
    exit 0
  fi
  sleep 1
done
echo "冒烟失败: 打包产物未在 30 秒内启动，日志见 /tmp/vconv_smoke_app.log" >&2
exit 1
