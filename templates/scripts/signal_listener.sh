#!/bin/bash
# signal_listener.sh — Listens for Signal messages and routes them
# Started via pm2: pm2 start signal_listener.sh --name "companion-signal"
#
# IMPORTANT LESSONS (from debugging):
# - NEVER pass Signal message content through bash string interpolation
# - Python writes to temp file, bash reads with sed — this is the safe pattern
# - signal-cli uses -o json (not --json) on v0.13.21 — check your version
# - signal-cli is single-process — all sends need flock serialization
# - ALL claude calls MUST use < /dev/null

COMPANION_HOME="/media/YOUR_USERNAME/CompanionHome"
SIGNAL_CONFIG="$COMPANION_HOME/scripts/signal_config.sh"

source "$SIGNAL_CONFIG"

export PATH="/home/YOUR_USERNAME/.cargo/bin:/home/YOUR_USERNAME/.npm-global/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

HANDLE_MSG="$COMPANION_HOME/scripts/handle_message.sh"
HANDLE_TASK="$COMPANION_HOME/tasks/handle_task.sh"
QUEUE_FILE="$COMPANION_HOME/tasks/task_queue.json"

# ─── Helper: Send Signal message (flock prevents concurrent signal-cli access) ───
send_signal() {
    local MSG="$1"
    (
        flock -x -w 30 200
        signal-cli -a "$COMPANION_NUMBER" send -m "$MSG" "$HUMAN_NUMBER" 2>/dev/null
    ) 200>/tmp/signal_send.lock
}

echo "Signal listener started. Waiting for messages..."

while true; do
    # Receive messages (5 second timeout per poll)
    # NOTE: -o json is a GLOBAL flag — goes before the subcommand
    RAW=$(signal-cli -a "$COMPANION_NUMBER" -o json receive -t 5 2>/dev/null)

    if [ -z "$RAW" ]; then
        continue
    fi

    # Parse each message using python → temp file (safe from special chars)
    echo "$RAW" | while IFS= read -r LINE; do
        # Skip empty lines
        [ -z "$LINE" ] && continue

        # Python extracts sender and body to temp file
        # This avoids ALL bash interpolation of message content
        python3 -c "
import json, sys
try:
    msg = json.loads(sys.stdin.read())
    envelope = msg.get('envelope', {})
    sender = envelope.get('sourceNumber', '') or envelope.get('source', '')
    data = envelope.get('dataMessage', {})
    body = data.get('message', '') if data else ''
    if sender and body:
        with open('/tmp/signal_parsed.txt', 'w') as f:
            f.write(sender + '\n')
            f.write(body + '\n')
        print('OK')
    else:
        print('SKIP')
except:
    print('SKIP')
" <<< "$LINE"

        PARSE_RESULT=$?
        if [ "$(cat /tmp/signal_parsed.txt 2>/dev/null | wc -l)" -lt 2 ]; then
            continue
        fi

        SENDER=$(sed -n '1p' /tmp/signal_parsed.txt)
        BODY=$(sed -n '2p' /tmp/signal_parsed.txt)

        # Only process messages from the human
        if [ "$SENDER" != "$HUMAN_NUMBER" ]; then
            continue
        fi

        echo "Message from $SENDER: $(echo "$BODY" | head -c 50)..."

        # ─── Route: Task commands ───
        # Matches: task: description, do: description, task:project: description
        if echo "$BODY" | grep -qi "^task:\|^do:"; then
            # Extract project if specified (task:projectname: description)
            TASK_PROJECT=$(echo "$BODY" | python3 -c "
import sys
body = sys.stdin.read().strip()
parts = body.split(':', 2)
if len(parts) >= 3 and parts[0].lower() in ('task', 'do'):
    # task:project: description
    project = parts[1].strip()
    if project and not project[0].isspace():
        print(project)
    else:
        print('local')
else:
    print('local')
")
            TASK_DESC=$(echo "$BODY" | sed 's/^[Tt]ask:[^:]*:\s*//;s/^[Tt]ask:\s*//;s/^[Dd]o:\s*//')

            RESULT=$(bash "$HANDLE_TASK" "$TASK_DESC" "$TASK_PROJECT")
            if echo "$RESULT" | grep -q "^QUEUED:"; then
                TASK_ID=$(echo "$RESULT" | sed 's/QUEUED://')
                send_signal "📋 Task queued (#$TASK_ID): $TASK_DESC [$TASK_PROJECT]"
            else
                send_signal "❌ Failed to queue task: $RESULT"
            fi

        # ─── Route: Status check ───
        elif echo "$BODY" | grep -qi "^status$"; then
            STATUS=$(python3 -c "
import json
with open('$QUEUE_FILE') as f:
    tasks = json.load(f)
running = [t for t in tasks if t['status'] == 'running']
pending = [t for t in tasks if t['status'] == 'pending']
if running:
    t = running[0]
    print(f\"🔄 Running: {t['description']} [{t['project']}]\")
elif pending:
    print(f\"⏳ {len(pending)} task(s) pending. Next: {pending[0]['description']}\")
else:
    print('💤 No tasks running or pending.')
")
            send_signal "$STATUS"

        # ─── Route: Task history ───
        elif echo "$BODY" | grep -qi "^tasks$"; then
            HISTORY=$(python3 -c "
import json
with open('$QUEUE_FILE') as f:
    tasks = json.load(f)
recent = tasks[-5:] if len(tasks) > 5 else tasks
lines = []
for t in reversed(recent):
    icon = {'pending':'⏳','running':'🔄','completed':'✅','tested':'✅','pushed':'🚀','failed':'❌','timeout':'⏱','cancelled':'🚫'}.get(t['status'], '❓')
    lines.append(f\"{icon} {t['id'][:6]} [{t['project']}] {t['status']}: {t['description'][:40]}\")
print('\n'.join(lines) if lines else 'No tasks yet.')
")
            send_signal "$HISTORY"

        # ─── Route: Cancel running task ───
        elif echo "$BODY" | grep -qi "^cancel$"; then
            # Kill any running claude process
            pkill -f "claude.*--dangerously-skip-permissions" 2>/dev/null
            python3 -c "
import json, fcntl
with open('$QUEUE_FILE', 'r+') as f:
    fcntl.flock(f, fcntl.LOCK_EX)
    tasks = json.load(f)
    for t in tasks:
        if t['status'] == 'running':
            t['status'] = 'cancelled'
    f.seek(0)
    f.truncate()
    json.dump(tasks, f, indent=2)
    fcntl.flock(f, fcntl.LOCK_UN)
"
            send_signal "🚫 Cancelled running task."

        # ─── Route: Normal conversation ───
        else
            bash "$HANDLE_MSG" "$SENDER" "$BODY"
        fi

    done

    sleep 2
done
