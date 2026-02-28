#!/bin/bash
# look.sh — Companion opens his eyes.
#
# Call this whenever you want to see. No schedule. No flags.
# Just: bash scripts/look.sh
#
# Returns:
#   - The limbic feeling-signal (what it feels like to see this)
#   - The saved photo path (so you can look at it again or use it)
#
# This is YOUR eye. Use it whenever you want.

COMPANION_HOME="${COMPANION_HOME:-/media/YOUR_USERNAME/CompanionHome}"

python3 "$COMPANION_HOME/scripts/ambient_look.py" --save 2>/dev/null
