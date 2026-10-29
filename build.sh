#!/usr/bin/env bash
# Build script for Luna single binary using Nuitka
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

echo "=== Luna Nuitka Build ==="

# Check if Nuitka is installed
if ! command -v nuitka3 &> /dev/null && ! command -v nuitka &> /dev/null; then
    echo "Nuitka not found. Installing..."
    pip install nuitka
fi

# Use nuitka3 if available, otherwise nuitka
NUITKA=$(command -v nuitka3 || command -v nuitka)

# Build options
# --standalone: Create standalone distribution
# --onefile: Create single executable file
# --enable-plugin=pkg-resources: Handle pkg_resources
# --include-package: Include packages
# --follow-imports: Follow imports
# --remove-output: Clean previous build
# --output-dir: Output directory

echo "Building single binary..."

$NUITKA \
    --standalone \
    --onefile \
    --enable-plugin=pkg-resources \
    --enable-plugin=prompt_toolkit \
    --include-package=core \
    --include-package=tools \
    --include-package=session \
    --include-package=bridge \
    --include-package=ui \
    --include-package=config \
    --follow-imports \
    --remove-output \
    --output-dir=dist \
    --output-filename=luna \
    luna.py

echo ""
echo "Build complete!"
echo "Binary: dist/luna"
echo ""
echo "Test it:"
echo "  ./dist/luna agent list"
echo "  ./dist/luna --help"