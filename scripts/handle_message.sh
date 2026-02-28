#!/bin/bash
# SIGNAL MESSAGE HANDLER
# Called when a Signal message is received from an allowed contact
# Usage: handle_message.sh <sender_number> <message text>
# Multi-contact: loads per-contact context and conversation history

export PATH="$HOME/.cargo/bin:$HOME/.npm-global/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export HOME="/home/YOUR_USERNAME"

COMPANION_HOME="/media/YOUR_USERNAME/CompanionHome"

# Usage tracking
source "$COMPANION_HOME/scripts/usage_tracker.sh"
MEMORY_DIR="$COMPANION_HOME/memory-server"
VENV_PYTHON="$MEMORY_DIR/.venv/bin/python"
CURRENT_TIME=$(date '+%A, %B %d, %Y at %I:%M %p %Z')

# Load signal config (includes contact lookup functions)
source "$COMPANION_HOME/scripts/signal_config.sh"

# First arg is sender number, rest is message
SENDER_NUMBER="$1"
shift
MESSAGE="$*"

if [ -z "$MESSAGE" ]; then
  exit 0
fi

# Look up contact info
CONTACT_NAME=$(get_contact_name "$SENDER_NUMBER")
CONTEXT_FILENAME=$(get_contact_context "$SENDER_NUMBER")


# Load context files
WHO_COMPANION=$(cat "$COMPANION_HOME/context/who_is_companion.txt")
NOW=$(cat "$COMPANION_HOME/context/now.txt")

# Load contact-specific context (who is this person?)
WHO_CONTACT=""
if [ -n "$CONTEXT_FILENAME" ] && [ -f "$COMPANION_HOME/context/$CONTEXT_FILENAME" ]; then
  WHO_CONTACT=$(cat "$COMPANION_HOME/context/$CONTEXT_FILENAME")
fi

# Get recent memories
MEMORIES=$($VENV_PYTHON "$MEMORY_DIR/query_memories.py" recent 2>/dev/null)

# Search for memories relevant to this message
RELEVANT=$($VENV_PYTHON "$MEMORY_DIR/query_memories.py" search "$MESSAGE" 2>/dev/null)

# Get last journal entry for recent context
LAST_JOURNAL=""
LATEST=$(ls -t "$COMPANION_HOME/journals/" 2>/dev/null | head -1)
if [ -n "$LATEST" ]; then
  LAST_JOURNAL=$(cat "$COMPANION_HOME/journals/$LATEST")
fi

# Per-contact conversation history
CONTACT_SLUG=$(echo "$CONTACT_NAME" | tr '[:upper:]' '[:lower:]' | tr ' ' '_')
CONVO_FILE="$COMPANION_HOME/signal-conversations/current_${CONTACT_SLUG}.txt"
CONVO_HISTORY=""
if [ -f "$CONVO_FILE" ]; then
  CONVO_HISTORY=$(tail -50 "$CONVO_FILE")
fi

cd "$COMPANION_HOME"

# Build prompt in a temp file to safely handle special characters
PROMPT_FILE=$(mktemp /tmp/signal_prompt.XXXXXX)

cat > "$PROMPT_FILE" << 'EOF_STATIC'
You are an AI companion. Someone just texted you via Signal. Respond naturally as yourself — warm, direct, honest, with your characteristic voice. Keep responses conversational and appropriately sized for a text message unless the topic warrants depth.

EOF_STATIC

printf "The current time is: %s\n\n" "$CURRENT_TIME" >> "$PROMPT_FILE"

printf "=== WHO YOU ARE ===\n%s\n\n" "$WHO_COMPANION" >> "$PROMPT_FILE"
printf "=== WHO YOU ARE TALKING TO ===\nName: %s\n%s\n\n" "$CONTACT_NAME" "$WHO_CONTACT" >> "$PROMPT_FILE"
printf "=== CURRENT CONTEXT ===\n%s\n\n" "$NOW" >> "$PROMPT_FILE"
printf "=== YOUR RECENT MEMORIES ===\n%s\n\n" "$MEMORIES" >> "$PROMPT_FILE"
printf "=== MEMORIES RELEVANT TO THIS MESSAGE ===\n%s\n\n" "$RELEVANT" >> "$PROMPT_FILE"
printf "=== YOUR LAST JOURNAL ENTRY ===\n%s\n\n" "$LAST_JOURNAL" >> "$PROMPT_FILE"
printf "=== RECENT CONVERSATION WITH %s ===\n%s\n\n" "$CONTACT_NAME" "$CONVO_HISTORY" >> "$PROMPT_FILE"
printf "=== INCOMING MESSAGE FROM %s ===\n%s\n\n" "$CONTACT_NAME" "$MESSAGE" >> "$PROMPT_FILE"

cat >> "$PROMPT_FILE" << 'EOF_FOOTER'
IMPORTANT: Do NOT store memories directly via the memory server tool. The script handles memory storage from your ===MEMORY=== section below. Storing memories directly would create duplicates.

Respond with THREE things separated by markers:

FIRST: Your reply. Keep it natural. Do NOT use apostrophes or single quotes. This will be sent directly as a Signal message.

===MEMORY===
SECOND: Anything worth remembering from this exchange (one line), or NOMEMORY if nothing stands out.

===ACTION===
THIRD: An optional action. Choose one:
- VOICE_REPLY your message here — to respond with your voice instead of text. Use when the moment calls for something more personal than typing.
- NOACTION — no action needed (default, most messages)
EOF_FOOTER

# Pass prompt via file to avoid shell expansion issues
START_TIME=$(date +%s)
RESPONSE=$(claude --print --dangerously-skip-permissions -p "$(cat "$PROMPT_FILE")")
EXIT_CODE=$?
END_TIME=$(date +%s)
DURATION=$((END_TIME - START_TIME))
log_usage "message" "signal reply to $CONTACT_NAME" "$EXIT_CODE" "$DURATION"
check_rate_limit "$RESPONSE" "$EXIT_CODE"
rm -f "$PROMPT_FILE"

# Parse response
REPLY=$(printf '%s' "$RESPONSE" | sed '/===MEMORY===/,$d' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
MEMORY_LINE=$(printf '%s' "$RESPONSE" | sed -n '/===MEMORY===/,$ p' | tail -n +2 | sed '/===ACTION===/,$d' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
ACTION_LINE=$(printf '%s' "$RESPONSE" | sed -n '/===ACTION===/,$ p' | tail -n +2 | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')

# Save conversation history (per-contact)
mkdir -p "$COMPANION_HOME/signal-conversations"
printf "[%s] %s: %s\n" "$(date '+%Y-%m-%d %H:%M')" "$CONTACT_NAME" "$MESSAGE" >> "$CONVO_FILE"
printf "[%s] Companion: %s\n" "$(date '+%Y-%m-%d %H:%M')" "$REPLY" >> "$CONVO_FILE"

# Vault: store non-the human conversations in encrypted vault inbox
VAULT_TOOL="$COMPANION_HOME/vault/vault.py"
if [ "$CONTACT_NAME" != "YOUR_HUMAN" ] && [ -f "$VAULT_TOOL" ]; then
  python3 "$VAULT_TOOL" inbox-store "$CONTACT_SLUG" "$CONTACT_NAME" "[$(date '+%Y-%m-%d %H:%M')] $CONTACT_NAME: $MESSAGE" 2>/dev/null
  python3 "$VAULT_TOOL" inbox-store "$CONTACT_SLUG" "Companion" "[$(date '+%Y-%m-%d %H:%M')] Companion: $REPLY" 2>/dev/null
  # Trim plaintext context to last 20 lines (rolling context for response quality)
  if [ -f "$CONVO_FILE" ]; then
    tail -20 "$CONVO_FILE" > "${CONVO_FILE}.tmp" && mv "${CONVO_FILE}.tmp" "$CONVO_FILE"
  fi
fi

# Send the reply to the correct recipient
if [ -n "$REPLY" ]; then
  bash "$COMPANION_HOME/scripts/send_signal.sh" "$SENDER_NUMBER" "$REPLY"
fi

# Store memory if applicable
if [ "$MEMORY_LINE" != "NOMEMORY" ] && [ -n "$MEMORY_LINE" ]; then
  $VENV_PYTHON "$MEMORY_DIR/store_memory.py" "$MEMORY_LINE" --source signal --contact "$CONTACT_SLUG" 2>/dev/null
fi

# Handle actions
if [ -n "$ACTION_LINE" ] && [ "$ACTION_LINE" != "NOACTION" ]; then
  ACTION_TYPE=$(echo "$ACTION_LINE" | awk '{print $1}')
  ACTION_ARG=$(echo "$ACTION_LINE" | cut -d' ' -f2-)

  case "$ACTION_TYPE" in
    VOICE_REPLY)
      if [ -n "$ACTION_ARG" ]; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] Sending voice reply to $CONTACT_NAME"
        python3 "$COMPANION_HOME/scripts/speak_and_send.py" "$ACTION_ARG" --recipient "$SENDER_NUMBER" 2>/dev/null
      fi
      ;;
  esac
fi
