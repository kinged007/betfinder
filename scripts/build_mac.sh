#!/bin/bash
set -e

# Configuration
APP_NAME="BetFinderApp"
BUILD_DIR="packages"

echo "🚀 Starting Local Mac Build for $APP_NAME..."

# 1. Check for uv
if ! command -v uv &> /dev/null; then
    echo "❌ uv not found. Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    source $HOME/.cargo/env
fi

# 2. Sync Dependencies
echo "📦 Syncing dependencies..."
uv sync --all-extras --dev

# 3. Clean previous build
echo "🧹 Cleaning previous builds..."
rm -rf $BUILD_DIR
rm -rf build

# 4. Build App
echo "🔨 Building Application..."
# Ensure we use x86_64 architecture if running on Apple Silicon but targeting Intel
# Note: For local builds, usually we just build for the host arch. 
# If you want to force Intel on M1/M2/M3: arch -x86_64 uv run build
uv run build

echo "✅ Build Complete!"
echo "📂 Artifacts are located in $BUILD_DIR/"
