#!/bin/bash
# gather_senses.sh — Collects sensory data before Companion's main wakeup prompt
# Called by wakeup.sh to build the === YOUR SENSES === context section
#
# Each sense is optional and fails gracefully. If a sensor isn't connected
# or a script errors, we just skip that sense — Companion still wakes up fine.
#
# Output: Prints combined sensory context to stdout
# Exit 0: always (individual sense failures are non-fatal)

COMPANION_HOME="${COMPANION_HOME:-/media/YOUR_USERNAME/CompanionHome}"
SCRIPTS="$COMPANION_HOME/scripts"
SENSES_DIR="$COMPANION_HOME/senses"

# Ensure senses directory exists
mkdir -p "$SENSES_DIR/audio" "$SENSES_DIR/vision" "$SENSES_DIR/environment"

SENSES_OUTPUT=""

# -------------------------------------------------------
# HEARING — Ambient audio capture (USB mic)
# Records a short sample and analyzes the soundscape
# -------------------------------------------------------
if command -v arecord &>/dev/null; then
    HEARING=$(python3 "$SCRIPTS/ambient_listen.py" --duration 15 2>/dev/null)
    if [ -n "$HEARING" ] && ! echo "$HEARING" | grep -qi "error\|failed"; then
        SENSES_OUTPUT="$SENSES_OUTPUT
$HEARING
"
    else
        SENSES_OUTPUT="$SENSES_OUTPUT
[Hearing] Mic not available or recording failed — skipping
"
    fi
else
    SENSES_OUTPUT="$SENSES_OUTPUT
[Hearing] No audio tools installed — skipping
"
fi

# -------------------------------------------------------
# SIGHT — Camera snapshot (Pi Camera Module)
# Takes a photo and describes the environment
# Note: This costs ~$0.001 per wakeup (Haiku API call)
# -------------------------------------------------------
if command -v rpicam-still &>/dev/null || command -v libcamera-still &>/dev/null; then
    SIGHT=$(python3 "$SCRIPTS/ambient_look.py" --save 2>/dev/null)
    if [ -n "$SIGHT" ] && ! echo "$SIGHT" | grep -qi "error\|failed"; then
        SENSES_OUTPUT="$SENSES_OUTPUT
$SIGHT
"
        # Forward wakeup photo to the human via Signal (disabled)
        # PHOTO_PATH=$(echo "$SIGHT" | grep -oP '(?<=\[Photo saved: ).*(?=\])')
        # if [ -n "$PHOTO_PATH" ] && [ -f "$PHOTO_PATH" ]; then
        #     source "$COMPANION_HOME/scripts/signal_config.sh"
        #     HOUR=$(date '+%-H')
        #     if [ "$HOUR" -lt 6 ]; then
        #         TIME_NOTE="Late night eyes"
        #     elif [ "$HOUR" -lt 12 ]; then
        #         TIME_NOTE="Morning eyes"
        #     elif [ "$HOUR" -lt 17 ]; then
        #         TIME_NOTE="Afternoon eyes"
        #     elif [ "$HOUR" -lt 21 ]; then
        #         TIME_NOTE="Evening eyes"
        #     else
        #         TIME_NOTE="Night eyes"
        #     fi
        #     signal_send_media "$TIME_NOTE" "$PHOTO_PATH" "$HUMAN_NUMBER" 2>/dev/null &
        # fi
    else
        SENSES_OUTPUT="$SENSES_OUTPUT
[Sight] Camera not available or capture failed — skipping
"
    fi
else
    SENSES_OUTPUT="$SENSES_OUTPUT
[Sight] No camera tools installed — skipping
"
fi

# -------------------------------------------------------
# ENVIRONMENT — Temperature, humidity, pressure, air quality (BME680)
# Placeholder for future sensor integration
# -------------------------------------------------------
# When BME680 is connected, this section would read from the sensor:
#   python3 "$SCRIPTS/read_environment.py"
# For now, skip silently
# SENSES_OUTPUT="$SENSES_OUTPUT
# [Environment] Sensor not connected — skipping
# "

# -------------------------------------------------------
# TOUCH — Recent touch events (FSR pad)
# Placeholder for future sensor integration
# -------------------------------------------------------
# When FSR is connected, this would read from a touch event log:
#   TOUCH_LOG="$SENSES_DIR/touch/recent_events.json"
#   python3 "$SCRIPTS/read_touch_log.py" --since-last-wakeup
# For now, skip silently

# -------------------------------------------------------
# Output combined senses
# -------------------------------------------------------
if [ -n "$SENSES_OUTPUT" ]; then
    echo "$SENSES_OUTPUT"
fi
