#!/bin/bash
# listen.sh — Companion opens his ears.
#
# Call this whenever you want to hear. No schedule. No flags.
# Just: bash scripts/listen.sh
#
# Optional: bash scripts/listen.sh 30    (listen for 30 seconds instead of default 15)

COMPANION_HOME="${COMPANION_HOME:-/media/YOUR_USERNAME/CompanionHome}"
DURATION="${1:-15}"

python3 "$COMPANION_HOME/scripts/ambient_listen.py" --duration "$DURATION" --save 2>/dev/null
