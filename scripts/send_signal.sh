#!/bin/bash
# SIGNAL MESSAGE SENDER
# Sends a message to a contact via Signal
# Usage: send_signal.sh <recipient_number> <message text>
#    OR: send_signal.sh <message text>  (defaults to HUMAN_NUMBER for backward compat)

COMPANION_HOME="/media/YOUR_USERNAME/CompanionHome"
CONFIG_FILE="$COMPANION_HOME/scripts/signal_config.sh"

# Load config
if [ -f "$CONFIG_FILE" ]; then
  source "$CONFIG_FILE"
else
  echo "ERROR: No signal config found at $CONFIG_FILE"
  exit 1
fi

# If first arg looks like a phone number (+...) or UUID (hex-hex-hex-hex-hex), it's a recipient
if [[ "$1" == +* ]] || [[ "$1" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]]; then
  RECIPIENT="$1"
  shift
  MSG="$*"
else
  # Backward compat: no number given, send to the human
  RECIPIENT="$HUMAN_NUMBER"
  MSG="$*"
fi

flock -w 30 /tmp/signal_send.lock \
    signal-cli -a "$COMPANION_NUMBER" send -m "$MSG" "$RECIPIENT"
