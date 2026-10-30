#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p .local
chmod 700 .local
export UV_CACHE_DIR="$PWD/.local/uv-cache"
command -v uv >/dev/null || { echo 'Install uv from https://docs.astral.sh/uv/getting-started/installation/'; exit 1; }
command -v ffmpeg >/dev/null && command -v ffprobe >/dev/null || { echo 'Install FFmpeg and ffprobe first.'; exit 1; }
command -v google-chrome >/dev/null || { echo 'Install Google Chrome first.'; exit 1; }
uv venv --python /usr/bin/python3 .venv --allow-existing
if [[ -f requirements.lock ]]; then
  uv pip sync --python .venv/bin/python requirements.lock
else
  uv pip install --python .venv/bin/python -r requirements.txt
fi
if [[ ! -x .runtime/node/node_modules/node/bin/node ]]; then
  npm install --prefix .runtime/node node@24.22.2 --save-exact --no-audit --no-fund
fi
export PATH="$PWD/.runtime/node/node_modules/.bin:$PATH"
if [[ ! -f .runtime/n8n/node_modules/n8n/bin/n8n ]]; then
  npm install --prefix .runtime/n8n n8n@2.45.7 --save-exact --legacy-peer-deps --no-audit --no-fund
fi
python3 scripts/n8n_workflows.py
echo 'Local setup ready. Run ./scripts/start.sh'
echo 'For free local story planning: ollama pull qwen2.5:3b'
