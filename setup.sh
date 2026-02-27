#!/bin/bash
# AI Companion Pi - Quick Setup
# Run this on your Raspberry Pi to set up the directory structure

set -e

echo "=== AI Companion Pi Setup ==="
echo ""

# Get username
USER=$(whoami)
echo "Running as: $USER"

# Get companion home path
read -p "Companion home directory [/media/$USER/CompanionHome]: " COMPANION_HOME
COMPANION_HOME=${COMPANION_HOME:-"/media/$USER/CompanionHome"}

echo ""
echo "Setting up at: $COMPANION_HOME"
echo ""

# Create directories
echo "Creating directories..."
mkdir -p "$COMPANION_HOME"/{context,journals,scripts,memory-server,signal-conversations,window/content,messageboard/files,creations/{code,art,writing,experiments,keepsakes}}

# Copy scripts
echo "Copying scripts..."
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cp "$SCRIPT_DIR/scripts/"*.sh "$COMPANION_HOME/scripts/"
chmod +x "$COMPANION_HOME/scripts/"*.sh

# Copy context templates
echo "Copying context templates..."
cp "$SCRIPT_DIR/context/"*.template.txt "$COMPANION_HOME/context/"

# Copy memory server
echo "Copying memory server..."
cp "$SCRIPT_DIR/memory-server/"*.py "$COMPANION_HOME/memory-server/"

# Copy window dashboard
echo "Copying web dashboard..."
cp "$SCRIPT_DIR/window/window.py" "$COMPANION_HOME/window/"
cp "$SCRIPT_DIR/window/icon.svg" "$COMPANION_HOME/window/"
cp "$SCRIPT_DIR/window/status.template.json" "$COMPANION_HOME/window/status.json"

# Update paths in all scripts
echo "Updating paths..."
find "$COMPANION_HOME" -name "*.sh" -exec sed -i "s|YOUR_USERNAME|$USER|g" {} \;
find "$COMPANION_HOME" -name "*.py" -exec sed -i "s|YOUR_USERNAME|$USER|g" {} \;

# Set up Python environment for memory server
echo ""
echo "Setting up Python environment..."
cd "$COMPANION_HOME/memory-server"
python3 -m venv .venv
source .venv/bin/activate
pip install mcp sentence-transformers numpy uvicorn flask markdown Pillow
deactivate

# Install creative tools
echo ""
echo "Installing creative tools (ImageMagick, ffmpeg)..."
sudo apt install -y imagemagick ffmpeg

echo ""
echo "=== Setup Complete ==="
echo ""
echo "Next steps:"
echo "  1. Edit your identity files:"
echo "     - $COMPANION_HOME/context/who_is_companion.txt"
echo "     - $COMPANION_HOME/context/who_is_human.txt"
echo "     - $COMPANION_HOME/context/now.txt"
echo "     (Templates have been copied — rename .template.txt to .txt and customize)"
echo ""
echo "  2. Install Claude Code:"
echo "     npm install -g @anthropic-ai/claude-code"
echo ""
echo "  3. Set up the cron job:"
echo "     crontab -e"
echo "     Add: 0 */4 * * * $COMPANION_HOME/scripts/wakeup.sh"
echo ""
echo "  4. (Optional) Set up Signal messaging:"
echo "     See docs/signal-setup.md"
echo ""
echo "  5. Connect Claude Code to the memory server:"
echo "     claude mcp add memory $COMPANION_HOME/memory-server/.venv/bin/python $COMPANION_HOME/memory-server/memory_server.py"
echo ""
echo "  6. Start the web dashboard:"
echo "     pm2 start $COMPANION_HOME/scripts/start_window.sh --name companion-window --interpreter bash"
echo "     Visit http://$(hostname -I | awk '{print $1}'):3000 from any device on your network"
echo "     Add to your phone's home screen for a PWA app experience"
echo ""
echo "Your companion's home is ready at: $COMPANION_HOME"
