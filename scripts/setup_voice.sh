#!/bin/bash
# setup_voice.sh — Install Whisper.cpp and Piper TTS for the companion voice notes
# Run once on the Pi
#
# Installs:
#   - Whisper.cpp (speech-to-text, local, free)
#   - Piper TTS (text-to-speech, local, free)
#   - Downloads default voice model
#   - ffmpeg (if not present)

COMPANION_HOME="${COMPANION_HOME:-/media/YOUR_USERNAME/CompanionHome}"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

pass() { echo -e "${GREEN}✓${NC} $1"; }
fail() { echo -e "${RED}✗${NC} $1"; }
info() { echo -e "${YELLOW}→${NC} $1"; }

echo "=================================="
echo " the companion Voice — Setup"
echo "=================================="
echo ""

# --- ffmpeg ---
echo "--- ffmpeg ---"
if command -v ffmpeg &>/dev/null; then
    pass "ffmpeg already installed"
else
    info "Installing ffmpeg..."
    sudo apt update -qq && sudo apt install -y ffmpeg
    command -v ffmpeg &>/dev/null && pass "ffmpeg installed" || fail "ffmpeg failed"
fi

# --- Whisper.cpp (Speech-to-Text) ---
echo ""
echo "--- Whisper.cpp (speech-to-text) ---"

WHISPER_DIR="$HOME/whisper.cpp"

if [ -f "$WHISPER_DIR/main" ] || [ -f "$WHISPER_DIR/build/bin/main" ]; then
    pass "Whisper.cpp already built"
else
    info "Cloning and building Whisper.cpp..."
    info "This will take a few minutes on Pi 5..."

    cd "$HOME"
    if [ ! -d "$WHISPER_DIR" ]; then
        git clone https://github.com/ggerganov/whisper.cpp.git
    fi

    cd "$WHISPER_DIR"
    make -j4

    if [ -f "$WHISPER_DIR/main" ]; then
        pass "Whisper.cpp built successfully"
    else
        # Try cmake build
        info "Trying cmake build..."
        mkdir -p build && cd build
        cmake .. && make -j4
        if [ -f "$WHISPER_DIR/build/bin/main" ]; then
            pass "Whisper.cpp built (cmake)"
        else
            fail "Whisper.cpp build failed"
            info "You may need: sudo apt install build-essential cmake"
        fi
    fi
fi

# Download model
echo ""
echo "--- Whisper model ---"

# Try small model first (better accuracy), fall back to base (faster)
SMALL_MODEL="$WHISPER_DIR/models/ggml-small.bin"
BASE_MODEL="$WHISPER_DIR/models/ggml-base.bin"

if [ -f "$SMALL_MODEL" ]; then
    pass "Small model already downloaded (best quality for Pi 5)"
elif [ -f "$BASE_MODEL" ]; then
    pass "Base model already downloaded"
    info "For better quality: bash $WHISPER_DIR/models/download-ggml-model.sh small"
else
    info "Downloading Whisper small model (~466MB)..."
    info "This gives the best accuracy on Pi 5 with 8GB RAM"
    cd "$WHISPER_DIR"

    if [ -f "models/download-ggml-model.sh" ]; then
        bash models/download-ggml-model.sh small
        if [ -f "$SMALL_MODEL" ]; then
            pass "Small model downloaded"
        else
            info "Small model failed, trying base model (~142MB)..."
            bash models/download-ggml-model.sh base
            [ -f "$BASE_MODEL" ] && pass "Base model downloaded" || fail "Model download failed"
        fi
    else
        fail "Model download script not found"
        info "Manual download: https://huggingface.co/ggerganov/whisper.cpp"
    fi
fi

# --- Piper TTS (Text-to-Speech) ---
echo ""
echo "--- Piper TTS (text-to-speech) ---"

if python3 -c "import piper" 2>/dev/null || command -v piper &>/dev/null; then
    pass "Piper TTS already installed"
else
    info "Installing Piper TTS..."
    pip install piper-tts --break-system-packages -q 2>/dev/null

    if python3 -c "import piper" 2>/dev/null || command -v piper &>/dev/null; then
        pass "Piper TTS installed"
    else
        fail "Piper TTS installation failed"
        info "Try: pip install piper-tts --break-system-packages"
        info "Or install from: https://github.com/rhasspy/piper/releases"
    fi
fi

# Download default voice
echo ""
echo "--- Voice model ---"

VOICE_DIR="$HOME/piper-voices"
mkdir -p "$VOICE_DIR"

VOICE_MODEL="$VOICE_DIR/en_US-lessac-medium.onnx"
VOICE_CONFIG="$VOICE_DIR/en_US-lessac-medium.onnx.json"

if [ -f "$VOICE_MODEL" ]; then
    pass "Default voice (lessac-medium) already downloaded"
else
    info "Downloading default voice model (lessac-medium)..."
    info "This is a warm, natural-sounding voice"

    # Piper voice downloads from HuggingFace
    VOICE_URL="https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/lessac/medium/en_US-lessac-medium.onnx"
    CONFIG_URL="https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json"

    wget -q -O "$VOICE_MODEL" "$VOICE_URL" 2>/dev/null
    wget -q -O "$VOICE_CONFIG" "$CONFIG_URL" 2>/dev/null

    if [ -f "$VOICE_MODEL" ] && [ -s "$VOICE_MODEL" ]; then
        pass "Voice model downloaded"
    else
        fail "Voice download failed"
        info "Manual download from: https://rhasspy.github.io/piper-samples/"
        info "Save .onnx and .onnx.json to $VOICE_DIR/"
        # Cleanup failed downloads
        rm -f "$VOICE_MODEL" "$VOICE_CONFIG" 2>/dev/null
    fi
fi

# --- Directory structure ---
echo ""
echo "--- Directories ---"
mkdir -p "$COMPANION_HOME/senses/audio/voice_notes_sent"
mkdir -p "$COMPANION_HOME/senses/audio/voice_notes_received"
pass "Voice directories created"

# --- Verify ---
echo ""
echo "--- Verification ---"

# Test Whisper
WHISPER_BIN=""
[ -f "$WHISPER_DIR/main" ] && WHISPER_BIN="$WHISPER_DIR/main"
[ -f "$WHISPER_DIR/build/bin/main" ] && WHISPER_BIN="$WHISPER_DIR/build/bin/main"

if [ -n "$WHISPER_BIN" ]; then
    pass "Whisper.cpp binary: $WHISPER_BIN"
else
    fail "Whisper.cpp binary not found"
fi

# Test Piper
if command -v piper &>/dev/null; then
    pass "Piper TTS: $(which piper)"
elif [ -f "$HOME/.local/bin/piper" ]; then
    pass "Piper TTS: $HOME/.local/bin/piper"
else
    fail "Piper binary not found"
fi

echo ""
echo "=================================="
echo " Setup complete!"
echo ""
echo " Test transcription:"
echo "   python3 $COMPANION_HOME/scripts/transcribe_audio.py /path/to/voice_note.ogg"
echo ""
echo " Test speaking:"
echo "   python3 $COMPANION_HOME/scripts/speak_and_send.py \"Hello the human\" --no-send"
echo ""
echo " Send a voice note:"
echo "   python3 $COMPANION_HOME/scripts/speak_and_send.py \"Hello from the companion\""
echo "=================================="
