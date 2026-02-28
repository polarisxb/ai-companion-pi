#!/bin/bash
# SIGNAL CONFIGURATION
# Companion has its own phone number via prepaid burner
COMPANION_NUMBER="+1AAAAAAAAAA"
# the human's number (primary human — kept for backward compat)
HUMAN_NUMBER="+1BBBBBBBBBB"

# Contacts registry
COMPANION_HOME="${COMPANION_HOME:-/media/YOUR_USERNAME/CompanionHome}"
CONTACTS_FILE="$COMPANION_HOME/contacts/contacts.json"

# Signal-cli attachment directory (where received files are saved)
SIGNAL_ATTACHMENTS_DIR="$HOME/.local/share/signal-cli/attachments"

# Companion's saved attachments (persistent, with proper names)
SAVED_ATTACHMENTS_DIR="$COMPANION_HOME/signal-attachments"

# --- CONTACT LOOKUP ---
# Supports both phone numbers (+1...) and UUIDs as sender identifiers.
# UUIDs are resolved to phone numbers via the uuid_map in contacts.json.

# Resolve a sender ID (phone or UUID) to its phone number key
# Usage: RESOLVED=$(resolve_contact_id "1e5fb2c5-...")
resolve_contact_id() {
    local ID="$1"
    if [ ! -f "$CONTACTS_FILE" ]; then
        echo "$ID"
        return
    fi
    python3 -c "
import json
with open('$CONTACTS_FILE') as f:
    data = json.load(f)
contacts = data.get('contacts', {})
uuid_map = data.get('uuid_map', {})
# If ID is directly a contact key, return it
if '$ID' in contacts:
    print('$ID')
# If ID is a UUID, resolve to phone number
elif '$ID' in uuid_map:
    print(uuid_map['$ID'])
else:
    print('$ID')
" 2>/dev/null
}

# Check if a phone number or UUID is an allowed contact
# Usage: is_allowed_contact "+1BBBBBBBBBB" && echo "yes"
is_allowed_contact() {
    local ID="$1"
    local RESOLVED=$(resolve_contact_id "$ID")
    if [ ! -f "$CONTACTS_FILE" ]; then
        [ "$RESOLVED" = "$HUMAN_NUMBER" ] && return 0 || return 1
    fi
    python3 -c "
import json, sys
with open('$CONTACTS_FILE') as f:
    contacts = json.load(f).get('contacts', {})
sys.exit(0 if '$RESOLVED' in contacts else 1)
" 2>/dev/null
}

# Get a contact's name from their number or UUID
# Usage: CONTACT_NAME=$(get_contact_name "+1BBBBBBBBBB")
get_contact_name() {
    local ID="$1"
    local RESOLVED=$(resolve_contact_id "$ID")
    if [ ! -f "$CONTACTS_FILE" ]; then
        [ "$RESOLVED" = "$HUMAN_NUMBER" ] && echo "YOUR_HUMAN" || echo "Unknown"
        return
    fi
    python3 -c "
import json
with open('$CONTACTS_FILE') as f:
    contacts = json.load(f).get('contacts', {})
c = contacts.get('$RESOLVED', {})
print(c.get('name', 'Unknown'))
" 2>/dev/null
}

# Get a contact's context filename
# Usage: CONTEXT_FILE=$(get_contact_context "+1BBBBBBBBBB")
get_contact_context() {
    local ID="$1"
    local RESOLVED=$(resolve_contact_id "$ID")
    if [ ! -f "$CONTACTS_FILE" ]; then
        echo "who_is_human.txt"
        return
    fi
    python3 -c "
import json
with open('$CONTACTS_FILE') as f:
    contacts = json.load(f).get('contacts', {})
c = contacts.get('$RESOLVED', {})
print(c.get('context_file', ''))
" 2>/dev/null
}

# --- SEND FUNCTIONS ---

# Send a text-only Signal message
# Usage: signal_send_text "Hello" ["+1recipient"]
# If no recipient, defaults to HUMAN_NUMBER (backward compat)
signal_send_text() {
    local MESSAGE="$1"
    local RECIPIENT="${2:-$HUMAN_NUMBER}"
    if [ -z "$MESSAGE" ]; then return 1; fi
    flock -w 30 /tmp/signal_send.lock \
        signal-cli -a "$COMPANION_NUMBER" send -m "$MESSAGE" \
        "$RECIPIENT" 2>/dev/null
}

# Send a Signal message with an attachment
# Usage: signal_send_media "caption" "/path/to/file" ["+1recipient"]
signal_send_media() {
    local MESSAGE="$1"
    local ATTACHMENT="$2"
    local RECIPIENT="${3:-$HUMAN_NUMBER}"

    if [ -z "$ATTACHMENT" ] || [ ! -f "$ATTACHMENT" ]; then
        echo "ERROR: Attachment not found: $ATTACHMENT" >&2
        return 1
    fi

    if [ -n "$MESSAGE" ]; then
        flock -w 30 /tmp/signal_send.lock \
            signal-cli -a "$COMPANION_NUMBER" send \
            "$RECIPIENT" -m "$MESSAGE" -a "$ATTACHMENT" \
            2>/dev/null
    else
        flock -w 30 /tmp/signal_send.lock \
            signal-cli -a "$COMPANION_NUMBER" send \
            "$RECIPIENT" -m "" -a "$ATTACHMENT" \
            2>/dev/null
    fi
}
