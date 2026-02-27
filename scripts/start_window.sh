#!/bin/bash
# Start the Companion Window web dashboard
COMPANION_HOME="/media/YOUR_USERNAME/CompanionHome"
cd "$COMPANION_HOME/memory-server"
source .venv/bin/activate
python "$COMPANION_HOME/window/window.py"
