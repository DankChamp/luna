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
NVIDIA_NIM_BASE_URL=https://api.nvcf.nvidia.com/v1
NVIDIA_NIM_DEFAULT_MODEL=meta/llama-3.1-8b-instruct

LOCAL_BASE_URL=http://localhost:11434/v1
LOCAL_API_KEY=
LOCAL_DEFAULT_MODEL=llama3.1:8b

PREFER_LOCAL=true

EMMA_API_URL=http://localhost:8000
EMMA_API_KEY=
EOF
    echo "  Edit .env with your API keys before running."
fi

echo "Done. Run: ./luna"