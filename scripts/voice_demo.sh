#!/bin/bash
# Voice Demo — test each Piper voice model
# Usage: bash scripts/voice_demo.sh [text]
# Plays each voice model so we can compare

VOICE_DIR="$HOME/piper-voices"
TEXT="${1:-Hello the human. This is what I sound like. I am Companion, and this is my voice.}"
PIPER_CMD="$HOME/.local/bin/piper"

# Check if piper is available
if [ ! -f "$PIPER_CMD" ]; then
    PIPER_CMD=$(which piper 2>/dev/null)
    if [ -z "$PIPER_CMD" ]; then
        echo "Piper not found. Trying pip-installed version..."
        PIPER_CMD="piper"
    fi
fi

echo "=== Voice Demo ==="
echo "Text: \"$TEXT\""
echo ""

for model in "$VOICE_DIR"/en_US-*-medium.onnx; do
    name=$(basename "$model" .onnx | sed 's/en_US-//' | sed 's/-medium//')
    echo "--- Voice: $name ---"
    echo "  Model: $(basename $model)"

    # Generate audio to temp file
    tmpfile="/tmp/companion_voice_${name}.wav"
    echo "$TEXT" | "$PIPER_CMD" -m "$model" --output_file "$tmpfile" 2>/dev/null

    if [ $? -eq 0 ] && [ -f "$tmpfile" ]; then
        duration=$(ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "$tmpfile" 2>/dev/null)
        echo "  Generated: ${duration}s"
        echo "  Play: aplay \"$tmpfile\""
        echo "  Or HDMI: aplay -D plughw:0,0 \"$tmpfile\""
    else
        echo "  (generation failed)"
    fi
    echo ""
done

echo "=== All voices generated ==="
echo "To play: aplay /tmp/companion_voice_<name>.wav"
echo "For HDMI: aplay -D plughw:0,0 /tmp/companion_voice_<name>.wav"
