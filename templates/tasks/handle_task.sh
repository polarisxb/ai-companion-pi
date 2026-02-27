#!/bin/bash
# TASK SUBMISSION HANDLER
# Adds a coding task to the queue. Non-blocking — writes JSON and returns.
# Usage: handle_task.sh "task prompt" [project_name] [source]
# Called by signal_listener.sh or dashboard

export PATH="$HOME/.cargo/bin:$HOME/.npm-global/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export HOME="/home/YOUR_USERNAME"

COMPANION_HOME="/media/YOUR_USERNAME/CompanionHome"
TASKS_DIR="$COMPANION_HOME/tasks"
QUEUE_FILE="$TASKS_DIR/task_queue.json"
CONFIG_FILE="$TASKS_DIR/task_config.json"
LOCK_FILE="/tmp/task_queue.lock"

PROMPT="$1"
PROJECT="${2:-}"
SOURCE="${3:-signal}"

if [ -z "$PROMPT" ]; then
  echo "ERROR: No task prompt provided"
  exit 1
fi

# Read default project from config
if [ -z "$PROJECT" ]; then
  PROJECT=$(python3 -c "
import json
with open('$CONFIG_FILE') as f:
    print(json.load(f).get('default_project', 'companion'))
" 2>/dev/null)
fi

# Get project path and validate
PROJECT_PATH=$(python3 -c "
import json
with open('$CONFIG_FILE') as f:
    cfg = json.load(f)
    proj = cfg.get('projects', {}).get('$PROJECT', {})
    print(proj.get('path', ''))
" 2>/dev/null)

if [ -z "$PROJECT_PATH" ] || [ ! -d "$PROJECT_PATH" ]; then
  echo "ERROR: Unknown project '$PROJECT' or path doesn't exist"
  exit 1
fi

# Get defaults
MAX_TURNS=$(python3 -c "
import json
with open('$CONFIG_FILE') as f:
    print(json.load(f).get('defaults', {}).get('max_turns', 15))
" 2>/dev/null)

# Generate task ID
TASK_ID="t_$(date '+%Y%m%d_%H%M%S')"
CREATED=$(date -Iseconds)

# Add to queue with file locking
(
  flock -x 200

  python3 -c "
import json, sys

queue_file = '$QUEUE_FILE'
try:
    with open(queue_file) as f:
        queue = json.load(f)
except:
    queue = []

task = {
    'id': '$TASK_ID',
    'prompt': '''$PROMPT'''.replace(\"'''\", ''),
    'status': 'pending',
    'source': '$SOURCE',
    'project': '$PROJECT',
    'project_path': '$PROJECT_PATH',
    'branch': 'task/$TASK_ID',
    'max_turns': $MAX_TURNS,
    'created': '$CREATED',
    'started': None,
    'completed': None,
    'merged': None,
    'tested': None,
    'pushed': None,
    'duration_seconds': None,
    'summary': None,
    'test_result': None,
    'files_changed': [],
    'merge_commit': None,
    'error': None
}

queue.append(task)

with open(queue_file, 'w') as f:
    json.dump(queue, f, indent=2)

print('OK')
"

) 200>"$LOCK_FILE"

# Check if there's already a running task
RUNNING=$(python3 -c "
import json
with open('$QUEUE_FILE') as f:
    queue = json.load(f)
running = [t for t in queue if t['status'] == 'running']
pending = [t for t in queue if t['status'] == 'pending']
if running:
    print(f'Queued (1 running, {len(pending)} pending): $PROMPT')
elif len(pending) == 1:
    print(f'On it: $PROMPT')
else:
    print(f'Queued ({len(pending)} pending): $PROMPT')
" 2>/dev/null)

echo "$RUNNING"
