#!/bin/bash
export PATH="$HOME/.cargo/bin:$HOME/.npm-global/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export HOME="/home/YOUR_USERNAME"

COMPANION_HOME="/media/YOUR_USERNAME/CompanionHome"
TASKS_DIR="$COMPANION_HOME/tasks"
QUEUE_FILE="$TASKS_DIR/task_queue.json"
CONFIG_FILE="$TASKS_DIR/task_config.json"
LOCK_FILE="/tmp/task_queue.lock"
SIGNAL_LOCK="/tmp/signal_send.lock"
SEND_SCRIPT="$COMPANION_HOME/scripts/send_signal.sh"
LOG_DIR="$TASKS_DIR/logs"
POLL_INTERVAL=10

mkdir -p "$LOG_DIR"

echo "Task Runner starting..."
echo "Queue: $QUEUE_FILE"
echo "Polling every ${POLL_INTERVAL}s"

# On startup, mark any "running" tasks as interrupted
python3 -c "
import json
try:
    with open('$QUEUE_FILE') as f:
        queue = json.load(f)
    changed = False
    for task in queue:
        if task['status'] == 'running':
            task['status'] = 'interrupted'
            task['error'] = 'Task runner restarted while task was running'
            changed = True
    if changed:
        with open('$QUEUE_FILE', 'w') as f:
            json.dump(queue, f, indent=2)
        print('Marked interrupted tasks')
except:
    pass
" 2>/dev/null

send_signal() {
  local msg="$1"
  (
    flock -x 200
    bash "$SEND_SCRIPT" "$msg"
  ) 200>"$SIGNAL_LOCK"
}

while true; do
  # Find next pending task
  python3 -c "
import json
try:
    with open('$QUEUE_FILE') as f:
        queue = json.load(f)
    for task in queue:
        if task['status'] == 'pending':
            with open('/tmp/next_task.json', 'w') as f:
                json.dump(task, f)
            exit(0)
    try:
        import os
        os.remove('/tmp/next_task.json')
    except:
        pass
except:
    pass
" 2>/dev/null

  if [ ! -f /tmp/next_task.json ]; then
    sleep $POLL_INTERVAL
    continue
  fi

  # Read task fields
  TASK_ID=$(python3 -c "import json; t=json.load(open('/tmp/next_task.json')); print(t['id'])")
  TASK_PROMPT=$(python3 -c "import json; t=json.load(open('/tmp/next_task.json')); print(t['prompt'])")
  TASK_PROJECT=$(python3 -c "import json; t=json.load(open('/tmp/next_task.json')); print(t['project'])")
  TASK_PATH=$(python3 -c "import json; t=json.load(open('/tmp/next_task.json')); print(t['project_path'])")
  TASK_MAX_TURNS=$(python3 -c "import json; t=json.load(open('/tmp/next_task.json')); print(t['max_turns'])")
  TASK_BRANCH=$(python3 -c "import json; t=json.load(open('/tmp/next_task.json')); print(t['branch'])")
  rm -f /tmp/next_task.json

  # Check if project is pushable (uses git branches) or not (direct edit)
  PUSHABLE=$(python3 -c "
import json
with open('$CONFIG_FILE') as f:
    cfg = json.load(f)
p = cfg.get('projects',{}).get('$TASK_PROJECT',{}).get('pushable', False)
print('true' if p else 'false')
" 2>/dev/null)

  TIMEOUT_MIN=$(python3 -c "
import json
with open('$CONFIG_FILE') as f:
    print(json.load(f).get('defaults', {}).get('timeout_minutes', 15))
" 2>/dev/null)
  TIMEOUT_SEC=$((TIMEOUT_MIN * 60))

  TASK_LOG="$LOG_DIR/${TASK_ID}.log"
  START_TIME=$(date -Iseconds)
  START_EPOCH=$(date +%s)

  echo ""
  echo "========================================="
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting task: $TASK_ID"
  echo "Project: $TASK_PROJECT ($TASK_PATH) [pushable=$PUSHABLE]"
  echo "Prompt: $TASK_PROMPT"
  echo "Max turns: $TASK_MAX_TURNS, Timeout: ${TIMEOUT_MIN}m"
  echo "========================================="

  # Mark as running
  python3 -c "
import json
with open('$QUEUE_FILE') as f:
    queue = json.load(f)
for task in queue:
    if task['id'] == '$TASK_ID':
        task['status'] = 'running'
        task['started'] = '$START_TIME'
        break
with open('$QUEUE_FILE', 'w') as f:
    json.dump(queue, f, indent=2)
" 2>/dev/null

  # Change to project directory
  cd "$TASK_PATH" || {
    python3 -c "
import json
with open('$QUEUE_FILE') as f:
    queue = json.load(f)
for task in queue:
    if task['id'] == '$TASK_ID':
        task['status'] = 'failed'
        task['error'] = 'Project directory not found'
        break
with open('$QUEUE_FILE', 'w') as f:
    json.dump(queue, f, indent=2)
" 2>/dev/null
    send_signal "Task failed: directory not found for $TASK_PROJECT"
    continue
  }

  # Git setup — only for pushable projects
  if [ "$PUSHABLE" = "true" ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Setting up git branch..." | tee -a "$TASK_LOG"
    git stash 2>>"$TASK_LOG"
    git checkout main 2>>"$TASK_LOG"
    git pull origin main 2>>"$TASK_LOG" || true
    git checkout -b "$TASK_BRANCH" 2>>"$TASK_LOG" || git checkout -B "$TASK_BRANCH" 2>>"$TASK_LOG"
  else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Non-pushable project, working directly" | tee -a "$TASK_LOG"
  fi

  # Run Claude Code
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] Running Claude Code..." | tee -a "$TASK_LOG"

  CLAUDE_OUTPUT=$(timeout "$TIMEOUT_SEC" claude -p --dangerously-skip-permissions --max-turns "$TASK_MAX_TURNS" \
    "You are working on the '$TASK_PROJECT' project. Complete this task:

$TASK_PROMPT

Work carefully. Commit your changes when done with a clear commit message." < /dev/null 2>&1)
  EXIT_CODE=$?

  END_EPOCH=$(date +%s)
  DURATION=$((END_EPOCH - START_EPOCH))
  echo "$CLAUDE_OUTPUT" >> "$TASK_LOG"

  if [ $EXIT_CODE -eq 124 ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Task timed out" | tee -a "$TASK_LOG"
    if [ "$PUSHABLE" = "true" ]; then
      git checkout main 2>>"$TASK_LOG"
    fi
    python3 -c "
import json
with open('$QUEUE_FILE') as f:
    queue = json.load(f)
for task in queue:
    if task['id'] == '$TASK_ID':
        task['status'] = 'timeout'
        task['duration_seconds'] = $DURATION
        task['error'] = 'Timed out after ${TIMEOUT_MIN} minutes'
        break
with open('$QUEUE_FILE', 'w') as f:
    json.dump(queue, f, indent=2)
" 2>/dev/null
    send_signal "Task timed out after ${TIMEOUT_MIN}m: $(echo "$TASK_PROMPT" | head -c 80)"
    continue
  elif [ $EXIT_CODE -ne 0 ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Task failed (exit $EXIT_CODE)" | tee -a "$TASK_LOG"
    if [ "$PUSHABLE" = "true" ]; then
      git checkout main 2>>"$TASK_LOG"
    fi
    python3 -c "
import json
with open('$QUEUE_FILE') as f:
    queue = json.load(f)
for task in queue:
    if task['id'] == '$TASK_ID':
        task['status'] = 'failed'
        task['duration_seconds'] = $DURATION
        task['error'] = 'Claude Code exited with code $EXIT_CODE'
        break
with open('$QUEUE_FILE', 'w') as f:
    json.dump(queue, f, indent=2)
" 2>/dev/null
    send_signal "Task failed: $(echo "$TASK_PROMPT" | head -c 80)"
    continue
  fi

  # Success — get summary
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] Getting summary..." | tee -a "$TASK_LOG"
  SUMMARY=$(timeout 120 claude -p --dangerously-skip-permissions --max-turns 1 --continue \
    "Summarize what you just did in 2-3 sentences for a text message. List changed files. No apostrophes." < /dev/null 2>/dev/null)
  if [ -z "$SUMMARY" ]; then
    SUMMARY="Task completed. Check the branch for details."
  fi

  # Get changed files — only meaningful for pushable projects
  if [ "$PUSHABLE" = "true" ]; then
    FILES_JSON=$(git diff --name-only main 2>/dev/null | python3 -c "
import sys, json
files = [l.strip() for l in sys.stdin if l.strip()]
print(json.dumps(files))
" 2>/dev/null)
  else
    FILES_JSON="[]"
  fi
  if [ -z "$FILES_JSON" ]; then
    FILES_JSON="[]"
  fi

  COMPLETED_TIME=$(date -Iseconds)

  # For non-pushable projects, mark as "tested" directly (skip merge step)
  if [ "$PUSHABLE" = "true" ]; then
    FINAL_STATUS="completed"
  else
    FINAL_STATUS="tested"
  fi

  python3 << PYEOF
import json
with open('$QUEUE_FILE') as f:
    queue = json.load(f)
for task in queue:
    if task['id'] == '$TASK_ID':
        task['status'] = '$FINAL_STATUS'
        task['completed'] = '$COMPLETED_TIME'
        task['duration_seconds'] = $DURATION
        task['summary'] = """$SUMMARY"""
        task['files_changed'] = $FILES_JSON
        if '$FINAL_STATUS' == 'tested':
            task['tested'] = '$COMPLETED_TIME'
            task['merged'] = '$COMPLETED_TIME'
        break
with open('$QUEUE_FILE', 'w') as f:
    json.dump(queue, f, indent=2)
PYEOF

  if [ "$PUSHABLE" = "true" ]; then
    git checkout main 2>>"$TASK_LOG"
  fi

  echo "[$(date '+%Y-%m-%d %H:%M:%S')] Task complete in ${DURATION}s" | tee -a "$TASK_LOG"

  NOTIFY_MSG="Task done (${DURATION}s): $(echo "$SUMMARY" | head -c 300)"
  send_signal "$NOTIFY_MSG"

  sleep 2
done
