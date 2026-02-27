#!/bin/bash
# test_media_system.sh — Test each component of the signal media system
# Run this on the Pi AFTER deploying the scripts
# Usage: bash test_media_system.sh

COMPANION_HOME="/media/YOUR_USERNAME/CompanionHome"
SCRIPTS="$COMPANION_HOME/scripts"
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

pass() { echo -e "${GREEN}✓ PASS${NC}: $1"; }
fail() { echo -e "${RED}✗ FAIL${NC}: $1"; }
warn() { echo -e "${YELLOW}⚠ WARN${NC}: $1"; }
info() { echo -e "  → $1"; }

echo "========================================"
echo " Signal Media System — Component Tests"
echo "========================================"
echo ""

# --- Test 1: Directory structure ---
echo "--- Test 1: Directory structure ---"
if [ -d "$COMPANION_HOME/signal-attachments" ]; then
    pass "signal-attachments/ directory exists"
else
    mkdir -p "$COMPANION_HOME/signal-attachments"
    pass "signal-attachments/ directory created"
fi

# --- Test 2: Scripts exist ---
echo ""
echo "--- Test 2: Required scripts ---"
for SCRIPT in signal_config.sh signal_listener.sh parse_signal_message.py describe_image.py; do
    if [ -f "$SCRIPTS/$SCRIPT" ]; then
        pass "$SCRIPT exists"
    else
        fail "$SCRIPT NOT FOUND at $SCRIPTS/$SCRIPT"
    fi
done

# --- Test 3: API config ---
echo ""
echo "--- Test 3: API configuration ---"
if [ -f "$SCRIPTS/api_config.sh" ]; then
    # Check permissions
    PERMS=$(stat -c %a "$SCRIPTS/api_config.sh" 2>/dev/null)
    if [ "$PERMS" = "600" ]; then
        pass "api_config.sh exists with correct permissions (600)"
    else
        warn "api_config.sh exists but permissions are $PERMS (should be 600)"
        info "Fix with: chmod 600 $SCRIPTS/api_config.sh"
    fi

    # Check key format
    if grep -q "^ANTHROPIC_API_KEY=sk-ant-" "$SCRIPTS/api_config.sh" 2>/dev/null; then
        pass "API key format looks valid"
    else
        fail "API key not found or malformed in api_config.sh"
        info "Expected: ANTHROPIC_API_KEY=sk-ant-..."
    fi
else
    fail "api_config.sh NOT FOUND"
    info "Create it:"
    info "  echo 'ANTHROPIC_API_KEY=sk-ant-YOUR-KEY-HERE' > $SCRIPTS/api_config.sh"
    info "  chmod 600 $SCRIPTS/api_config.sh"
fi

# --- Test 4: Parse script ---
echo ""
echo "--- Test 4: Message parser ---"
# Create a fake signal-cli JSON message
TEST_JSON=$(mktemp /tmp/test_signal.XXXXXX)
cat > "$TEST_JSON" << 'EOF'
{"envelope":{"source":"+1YOUR_NUMBER","sourceNumber":"+1YOUR_NUMBER","sourceDevice":1,"timestamp":1739782800000,"dataMessage":{"timestamp":1739782800000,"message":"Hello the companion!","expiresInSeconds":0,"viewOnce":false,"mentions":[],"attachments":[]}}}
EOF

RESULT=$(python3 "$SCRIPTS/parse_signal_message.py" "$TEST_JSON" 2>/dev/null)
rm -f "$TEST_JSON"

if echo "$RESULT" | grep -q "SENDER=+1YOUR_NUMBER"; then
    pass "Parser extracts sender correctly"
else
    fail "Parser did not extract sender"
    info "Got: $RESULT"
fi

if echo "$RESULT" | grep -q "BODY=Hello the companion!"; then
    pass "Parser extracts body correctly"
else
    fail "Parser did not extract body"
fi

if echo "$RESULT" | grep -q "HAS_ATTACHMENT=no"; then
    pass "Parser correctly reports no attachment"
else
    fail "Parser attachment detection failed"
fi

# Test with attachment
TEST_JSON2=$(mktemp /tmp/test_signal.XXXXXX)
cat > "$TEST_JSON2" << 'EOF'
{"envelope":{"source":"+1YOUR_NUMBER","sourceNumber":"+1YOUR_NUMBER","sourceDevice":1,"timestamp":1739782800000,"dataMessage":{"timestamp":1739782800000,"message":"Look at this!","attachments":[{"contentType":"image/jpeg","filename":"sunset.jpg","id":"1309176955124639716","size":245760}]}}}
EOF

RESULT2=$(python3 "$SCRIPTS/parse_signal_message.py" "$TEST_JSON2" 2>/dev/null)
rm -f "$TEST_JSON2"

if echo "$RESULT2" | grep -q "HAS_ATTACHMENT=yes" && echo "$RESULT2" | grep -q "ATTACHMENT_TYPE=image"; then
    pass "Parser detects image attachment correctly"
else
    fail "Parser did not detect image attachment"
    info "Got: $RESULT2"
fi

# --- Test 5: signal_config.sh functions ---
echo ""
echo "--- Test 5: Config functions ---"
source "$SCRIPTS/signal_config.sh" 2>/dev/null
if type signal_send_media &>/dev/null; then
    pass "signal_send_media function available"
else
    fail "signal_send_media function not defined"
fi

if type signal_send_text &>/dev/null; then
    pass "signal_send_text function available"
else
    warn "signal_send_text function not defined (optional)"
fi

# --- Test 6: Outbound send test (optional, requires confirmation) ---
echo ""
echo "--- Test 6: Outbound send (manual) ---"
info "To test outbound sending, run:"
info "  source $SCRIPTS/signal_config.sh"
info "  signal_send_text 'Test from media system'"
info "  signal_send_media 'Test with image' '/path/to/any/image.png'"
info ""
info "Skip this if you just want to verify the scripts are in place."

# --- Test 7: describe_image.py (API test — costs ~$0.001) ---
echo ""
echo "--- Test 7: Image description API (optional) ---"
# Look for any image file to test with
TEST_IMAGE=$(find "$COMPANION_HOME/creations" -name "*.png" -o -name "*.jpg" 2>/dev/null | head -1)
if [ -z "$TEST_IMAGE" ]; then
    TEST_IMAGE=$(find "$COMPANION_HOME" -maxdepth 2 -name "*.png" -o -name "*.jpg" 2>/dev/null | head -1)
fi

if [ -n "$TEST_IMAGE" ] && [ -f "$SCRIPTS/api_config.sh" ]; then
    info "Found test image: $TEST_IMAGE"
    info "Running describe_image.py (this costs ~\$0.001)..."

    DESC=$(python3 "$SCRIPTS/describe_image.py" "$TEST_IMAGE" 2>/tmp/describe_err.txt)
    if [ -n "$DESC" ]; then
        pass "Image description API works!"
        info "Description: $DESC"
    else
        fail "Image description returned empty"
        ERR=$(cat /tmp/describe_err.txt 2>/dev/null)
        [ -n "$ERR" ] && info "Error: $ERR"
    fi
    rm -f /tmp/describe_err.txt
else
    if [ -z "$TEST_IMAGE" ]; then
        warn "No test image found — skipping API test"
    else
        warn "No api_config.sh — skipping API test"
    fi
fi

echo ""
echo "========================================"
echo " Tests complete. Check results above."
echo "========================================"
