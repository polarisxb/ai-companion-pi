#!/bin/bash
# SIGNAL LISTENER DAEMON
# Polls signal-cli for incoming messages and triggers response handler
# Run via pm2: pm2 start signal_listener.sh --name companion-signal --interpreter bash
#
# Updated: Feb 2026 — Multi-contact support + attachment support
# - Responds to any contact listed in contacts.json
# - Per-contact context and conversation history
# - Image attachments described via Claude API

export PATH="$HOME/.cargo/bin:$HOME/.npm-global/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export HOME="/home/YOUR_USERNAME"

COMPANION_HOME="/media/YOUR_USERNAME/CompanionHome"
CONFIG_FILE="$COMPANION_HOME/scripts/signal_config.sh"

# Load config (includes contact lookup functions)
if [ -f "$CONFIG_FILE" ]; then
  source "$CONFIG_FILE"
else
  echo "ERROR: No signal config found at $CONFIG_FILE"
  echo "Create it with COMPANION_NUMBER and HUMAN_NUMBER variables."
  exit 1
fi

HANDLER="$COMPANION_HOME/scripts/handle_message.sh"
PARSER="$COMPANION_HOME/scripts/parse_signal_message.py"
DESCRIBER="$COMPANION_HOME/scripts/describe_image.py"
POLL_INTERVAL=10  # seconds between checks
BUSY=false

# Where signal-cli downloads attachments
SIGNAL_ATT_DIR="$HOME/.local/share/signal-cli/attachments"
# Where we save them with proper names
SAVED_ATT_DIR="$COMPANION_HOME/signal-attachments"
mkdir -p "$SAVED_ATT_DIR"

echo "Companion Signal Listener starting..."
echo "Companion number: $COMPANION_NUMBER"
echo "Contacts file: $CONTACTS_FILE"
echo "Attachment support: enabled"
echo "Polling every ${POLL_INTERVAL}s"

# --- HELPER: Copy attachment from signal-cli to CompanionHome ---
copy_attachment() {
    local ATT_ID="$1"
    local CONTENT_TYPE="$2"
    local ORIG_NAME="$3"
    local TIMESTAMP=$(date +%s)

    local EXT=""
    case "$CONTENT_TYPE" in
        image/jpeg)  EXT=".jpg" ;;
        image/png)   EXT=".png" ;;
        image/gif)   EXT=".gif" ;;
        image/webp)  EXT=".webp" ;;
        audio/aac)   EXT=".aac" ;;
        audio/ogg*)  EXT=".ogg" ;;
        audio/mpeg)  EXT=".mp3" ;;
        video/mp4)   EXT=".mp4" ;;
        application/pdf) EXT=".pdf" ;;
        *)           EXT="" ;;
    esac

    local NAME=""
    if [ -n "$ORIG_NAME" ]; then
        NAME=$(basename "$ORIG_NAME" | sed 's/\.[^.]*$//')
    else
        NAME="$ATT_ID"
    fi

    local SOURCE="$SIGNAL_ATT_DIR/$ATT_ID"
    local DEST="$SAVED_ATT_DIR/${TIMESTAMP}_${NAME}${EXT}"

    local WAIT=0
    while [ ! -f "$SOURCE" ] && [ $WAIT -lt 5 ]; do
        sleep 1
        WAIT=$((WAIT + 1))
    done

    if [ -f "$SOURCE" ]; then
        cp "$SOURCE" "$DEST" 2>/dev/null
        if [ $? -eq 0 ]; then
            echo "$DEST"
            return 0
        fi
    fi

    echo ""
    return 1
}

# --- MAIN LOOP ---
while true; do
  if [ "$BUSY" = true ]; then
    sleep $POLL_INTERVAL
    continue
  fi

  # Receive pending messages as JSON
  MESSAGES=$(signal-cli -a "$COMPANION_NUMBER" -o json receive -t 5 2>/dev/null)

  if [ -n "$MESSAGES" ]; then
    echo "$MESSAGES" | while IFS= read -r line; do

      [ -z "$line" ] && continue

      # Write JSON to temp file
      JSON_TMP=$(mktemp /tmp/signal_msg.XXXXXX)
      printf '%s' "$line" > "$JSON_TMP"

      # Parse the message
      PARSED=$(python3 "$PARSER" "$JSON_TMP" 2>/dev/null)
      rm -f "$JSON_TMP"

      [ -z "$PARSED" ] && continue

      # Read parsed values
      SENDER=""
      BODY=""
      HAS_ATTACHMENT="no"
      ATTACHMENT_TYPE=""
      ATTACHMENT_CONTENT_TYPE=""
      ATTACHMENT_ID=""
      ATTACHMENT_FILENAME=""
      ATTACHMENT_SIZE=""

      while IFS='=' read -r key value; do
        case "$key" in
          SENDER)                  SENDER="$value" ;;
          BODY)                    BODY="$value" ;;
          HAS_ATTACHMENT)          HAS_ATTACHMENT="$value" ;;
          ATTACHMENT_TYPE)         ATTACHMENT_TYPE="$value" ;;
          ATTACHMENT_CONTENT_TYPE) ATTACHMENT_CONTENT_TYPE="$value" ;;
          ATTACHMENT_ID)           ATTACHMENT_ID="$value" ;;
          ATTACHMENT_FILENAME)     ATTACHMENT_FILENAME="$value" ;;
          ATTACHMENT_SIZE)         ATTACHMENT_SIZE="$value" ;;
        esac
      done <<< "$PARSED"

      # Decode escaped newlines in body
      BODY=$(printf '%b' "$BODY")

      # Get contact name for logging
      CONTACT_NAME=$(get_contact_name "$SENDER")
      echo "[$(date '+%Y-%m-%d %H:%M:%S')] Message from $CONTACT_NAME ($SENDER): $BODY"

      # Only respond to allowed contacts
      if is_allowed_contact "$SENDER"; then

        # --- ATTACHMENT PROCESSING ---
        AUGMENTED_BODY="$BODY"

        if [ "$HAS_ATTACHMENT" = "yes" ] && [ -n "$ATTACHMENT_ID" ]; then
          echo "[$(date '+%Y-%m-%d %H:%M:%S')] Attachment detected: $ATTACHMENT_TYPE ($ATTACHMENT_CONTENT_TYPE)"

          SAVED_PATH=$(copy_attachment "$ATTACHMENT_ID" "$ATTACHMENT_CONTENT_TYPE" "$ATTACHMENT_FILENAME")

          if [ -n "$SAVED_PATH" ] && [ -f "$SAVED_PATH" ]; then
            echo "[$(date '+%Y-%m-%d %H:%M:%S')] Attachment saved: $SAVED_PATH"

            case "$ATTACHMENT_TYPE" in
              image)
                echo "[$(date '+%Y-%m-%d %H:%M:%S')] Describing image via API..."
                IMAGE_DESC=$(python3 "$DESCRIBER" "$SAVED_PATH" --sender "$CONTACT_NAME" "$BODY" 2>/dev/null)

                if [ -n "$IMAGE_DESC" ]; then
                  AUGMENTED_BODY="${BODY}

[$CONTACT_NAME sent an image: ${IMAGE_DESC}]
[Image saved to: ${SAVED_PATH}]"
                else
                  AUGMENTED_BODY="${BODY}

[$CONTACT_NAME sent an image but it could not be described]
[Image saved to: ${SAVED_PATH}]"
                fi
                ;;
              audio)
                echo "[$(date '+%Y-%m-%d %H:%M:%S')] Processing voice note..."

                # --- STEP 1: Transcribe (what they said) ---
                TRANSCRIPT=""
                if [ -f "$COMPANION_HOME/scripts/transcribe_audio.py" ]; then
                  echo "[$(date '+%Y-%m-%d %H:%M:%S')] Transcribing..."
                  TRANSCRIPT=$(python3 "$COMPANION_HOME/scripts/transcribe_audio.py" "$SAVED_PATH" 2>/dev/null)
                  if [ -n "$TRANSCRIPT" ]; then
                    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Transcript: $TRANSCRIPT"
                  else
                    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Transcription unavailable"
                  fi
                fi

                # --- STEP 2: Deep listen / limbic processing (how it felt) ---
                LIMBIC=""
                if [ -f "$COMPANION_HOME/scripts/deep_listen.py" ]; then
                  echo "[$(date '+%Y-%m-%d %H:%M:%S')] Limbic processing (deep listen)..."
                  CMD=(python3 "$COMPANION_HOME/scripts/deep_listen.py" "$SAVED_PATH" --mode quick --save)
                  if [ -n "$TRANSCRIPT" ]; then
                    CMD+=(--transcript "$TRANSCRIPT")
                  fi
                  LIMBIC=$("${CMD[@]}" 2>/dev/null)
                  if [ -n "$LIMBIC" ]; then
                    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Limbic signal received"
                  else
                    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Deep listen unavailable (falling back to basic)"
                  fi
                fi

                # --- STEP 3: Build augmented body with ALL modalities ---
                if [ -n "$LIMBIC" ]; then
                  AUGMENTED_BODY="${BODY}

[Voice note from $CONTACT_NAME]
${LIMBIC}
[Audio saved to: ${SAVED_PATH}]"

                elif [ -n "$TRANSCRIPT" ]; then
                  AUGMENTED_BODY="${BODY}

[$CONTACT_NAME sent a voice note. They said: \"${TRANSCRIPT}\"]
[Audio saved to: ${SAVED_PATH}]"

                else
                  AUGMENTED_BODY="${BODY}

[$CONTACT_NAME sent a voice note but it could not be processed]
[Audio saved to: ${SAVED_PATH}]"
                fi
                ;;
              video)
                AUGMENTED_BODY="${BODY}

[$CONTACT_NAME sent a video — video processing is not available yet]
[Video saved to: ${SAVED_PATH}]"
                ;;
              pdf)
                AUGMENTED_BODY="${BODY}

[$CONTACT_NAME sent a PDF: ${ATTACHMENT_FILENAME:-document.pdf}]
[PDF saved to: ${SAVED_PATH}]"
                ;;
              *)
                AUGMENTED_BODY="${BODY}

[$CONTACT_NAME sent a file: ${ATTACHMENT_FILENAME:-unknown file} (${ATTACHMENT_CONTENT_TYPE})]
[File saved to: ${SAVED_PATH}]"
                ;;
            esac
          else
            AUGMENTED_BODY="${BODY}

[$CONTACT_NAME sent a ${ATTACHMENT_TYPE} attachment but it could not be saved]"
          fi
        fi

        # --- RESPOND ---
        if [ -n "$BODY" ] || [ "$HAS_ATTACHMENT" = "yes" ]; then
          echo "[$(date '+%Y-%m-%d %H:%M:%S')] Processing response to $CONTACT_NAME..."
          BUSY=true

          BODY_TMP=$(mktemp /tmp/signal_body.XXXXXX)
          printf '%s' "$AUGMENTED_BODY" > "$BODY_TMP"

          # Pass sender number as first arg so handler knows who to reply to
          bash "$HANDLER" "$SENDER" "$(cat "$BODY_TMP")"
          rm -f "$BODY_TMP"

          BUSY=false
          echo "[$(date '+%Y-%m-%d %H:%M:%S')] Response sent to $CONTACT_NAME."
        fi
      else
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] Ignoring message from unknown sender: $SENDER"
      fi
    done
  fi

  sleep $POLL_INTERVAL
done
