#!/usr/bin/env bash
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

echo "=== Luna Setup ==="

if [ ! -d .venv ]; then
    echo "Creating virtual environment..."
    python3 -m venv .venv
fi

echo "Installing dependencies..."
.venv/bin/pip install -r requirements.txt -q

if [ ! -f .env ]; then
    echo "Creating .env from template..."
    cat > .env << 'EOF'
NVIDIA_NIM_API_KEY=your_key_here
NVIDIA_NIM_BASE_URL=https://integrate.api.nvidia.com/v1
NVIDIA_NIM_DEFAULT_MODEL=meta/llama-3.1-8b-instruct

LOCAL_BASE_URL=http://localhost:11434/v1
LOCAL_API_KEY=
LOCAL_DEFAULT_MODEL=llama3.1:8b

PREFER_LOCAL=true

EMMA_API_URL=http://localhost:8000
# Shared secret between Luna and Emma. Set the SAME value on both sides.
# Luna sends it as a Bearer token when calling Emma, and requires it as a
# Bearer token on incoming /api/chat and /api/ingest calls when running
# `luna --serve`. Leave empty to keep the bridge fully disabled.
EMMA_API_KEY=
EOF
    echo "  Edit .env with your API keys before running."
fi

# .env holds API keys — keep it readable only by you.
chmod 600 .env 2>/dev/null || true

# Same for the local provider config Luna writes at runtime, if it exists.
if [ -f "$HOME/.luna/config.json" ]; then
    chmod 600 "$HOME/.luna/config.json" 2>/dev/null || true
fi

echo "Done. Run: ./luna"
echo ""
echo "To run Luna as a background service for Emma to talk to (systemd, Linux):"
echo "  see systemd/luna-bridge.service.example"