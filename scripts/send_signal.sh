#!/bin/bash
# SIGNAL MESSAGE SENDER
# Sends a message to the human via Signal

COMPANION_HOME="/media/YOUR_USERNAME/CompanionHome"
CONFIG_FILE="$COMPANION_HOME/scripts/signal_config.sh"

# Load config
if [ -f "$CONFIG_FILE" ]; then
  source "$CONFIG_FILE"
else
  echo "ERROR: No signal config found at $CONFIG_FILE"
  exit 1
fi

MSG="$*"
signal-cli -a "$COMPANION_NUMBER" send -m "$MSG" "$HUMAN_NUMBER"
