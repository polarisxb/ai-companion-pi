#!/bin/bash
# Start the Companion Window web dashboard
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPANION_HOME="${COMPANION_HOME:-$(cd "$SCRIPT_DIR/.." && pwd)}"

cd "$COMPANION_HOME"
if [ -f "$COMPANION_HOME/.venv/bin/activate" ]; then
  source "$COMPANION_HOME/.venv/bin/activate"
fi

python "$COMPANION_HOME/window/window.py"
