#!/bin/bash
# usage_tracker.sh — Source this file in wakeup.sh, handle_message.sh, task_runner.sh
# Logs every Claude Code call and watches for rate limits.
#
# Usage:
#   source "$COMPANION_HOME/scripts/usage_tracker.sh"
#   START_TIME=$(date +%s)
#   RESPONSE=$(claude --print --dangerously-skip-permissions -p "...")
#   EXIT_CODE=$?
#   END_TIME=$(date +%s)
#   DURATION=$((END_TIME - START_TIME))
#   log_usage "wakeup" "regular 4hr cycle" "$EXIT_CODE" "$DURATION"
#   check_rate_limit "$RESPONSE" "$EXIT_CODE"
#   check_usage

# --- Config ---
COMPANION_HOME="${COMPANION_HOME:-/media/YOUR_USERNAME/CompanionHome}"
USAGE_DIR="$COMPANION_HOME/usage"
USAGE_LOG="$USAGE_DIR/usage_log.csv"
USAGE_STATUS="$USAGE_DIR/usage_status.json"
USAGE_CHECK_SCRIPT="$COMPANION_HOME/scripts/usage_check.py"

# Source signal config if available (for SIGNAL_SENDER, HUMAN_NUMBER, send_signal)
if [ -f "$COMPANION_HOME/scripts/signal_config.sh" ]; then
    source "$COMPANION_HOME/scripts/signal_config.sh"
fi

# Ensure usage directory and CSV exist
mkdir -p "$USAGE_DIR"
if [ ! -f "$USAGE_LOG" ]; then
    echo "timestamp,type,description,exit_code,duration_sec" > "$USAGE_LOG"
fi

# --- Functions ---

log_usage() {
    # Log a Claude API call to the CSV
    # Args: type, description, exit_code, duration_seconds
    local TYPE="$1"
    local DESC="$2"
    local EXIT_CODE="${3:-0}"
    local DURATION="${4:-0}"
    local TIMESTAMP
    TIMESTAMP=$(date -Iseconds)

    # Sanitize description: remove commas, newlines, quotes
    DESC=$(echo "$DESC" | tr ',' ';' | tr '\n' ' ' | tr -d '"' | head -c 200)

    echo "$TIMESTAMP,$TYPE,$DESC,$EXIT_CODE,$DURATION" >> "$USAGE_LOG"
}

check_rate_limit() {
    # Check if a Claude response or exit code indicates rate limiting.
    # If so, send an immediate Signal alert.
    # Args: response_text, exit_code
    local RESPONSE="$1"
    local EXIT_CODE="$2"

    local RATE_LIMITED=false

    # Check exit code (non-zero can indicate rate limit)
    if [ "$EXIT_CODE" -ne 0 ]; then
        # Check response text for rate limit indicators
        if echo "$RESPONSE" | grep -qi "rate.limit\|too many requests\|429\|throttl\|overloaded"; then
            RATE_LIMITED=true
        fi
    fi

    # Also check response text regardless of exit code
    if echo "$RESPONSE" | grep -qi "rate.limit\|too many requests\|429"; then
        RATE_LIMITED=true
    fi

    if [ "$RATE_LIMITED" = true ]; then
        log_usage "rate_limit" "Rate limit detected" "$EXIT_CODE" "0"

        # Send immediate Signal alert
        if type send_signal &>/dev/null; then
            send_signal "🛑 I just got rate limited. You might want to ease up on Claude for a bit."
        elif [ -n "$SIGNAL_SENDER" ] && [ -n "$HUMAN_NUMBER" ]; then
            flock -w 10 /tmp/signal_send.lock \
                signal-cli -a "$SIGNAL_SENDER" send -m \
                "🛑 I just got rate limited. You might want to ease up on Claude for a bit." \
                "$HUMAN_NUMBER" 2>/dev/null
        fi
    fi
}

check_usage() {
    # Run the Python usage checker. If it returns a warning/critical message,
    # send it via Signal.
    if [ ! -f "$USAGE_CHECK_SCRIPT" ]; then
        return 0
    fi

    local ALERT_MSG
    ALERT_MSG=$(python3 "$USAGE_CHECK_SCRIPT" "$USAGE_LOG" "$USAGE_STATUS" 2>/dev/null)

    if [ -n "$ALERT_MSG" ]; then
        # Send the alert via Signal
        if type send_signal &>/dev/null; then
            send_signal "$ALERT_MSG"
        elif [ -n "$SIGNAL_SENDER" ] && [ -n "$HUMAN_NUMBER" ]; then
            flock -w 10 /tmp/signal_send.lock \
                signal-cli -a "$SIGNAL_SENDER" send -m "$ALERT_MSG" \
                "$HUMAN_NUMBER" 2>/dev/null
        fi
    fi
}
