# Debugging Guide
## Every Problem Encountered and How It Was Solved

This guide catalogs every bug hit during the initial build of the ai-companion-pi project (sessions 1–7, February 14–16, 2026), organized by subsystem. Each entry includes the session, symptoms, root cause, fix, and a key lesson.

**Coverage note:** This guide covers the core system build (filesystem, cron, Signal, memory server, dashboard, task system, networking). Systems built after session 7 — including the request system, sensory capabilities, Substack pipeline, multi-contact Signal, and media handling — are not yet covered here. Contributions welcome.
## TABLE OF CONTENTS

1. [Linux & Filesystem Issues](#1-linux--filesystem-issues)
2. [Claude Code Issues](#2-claude-code-issues)
3. [Cron Job Issues](#3-cron-job-issues)
4. [Signal CLI Issues](#4-signal-cli-issues)
5. [MCP Memory Server Issues](#5-mcp-memory-server-issues)
6. [Git & GitHub Issues](#6-git--github-issues)
7. [Dashboard / Web Server Issues](#7-dashboard--web-server-issues)
8. [Task System Issues (Session 5)](#8-task-system-issues-session-5)
9. [Network & Remote Access Issues](#9-network--remote-access-issues)
10. [Template vs Live System Issues](#10-template-vs-live-system-issues)

---

## 1. LINUX & FILESYSTEM ISSUES

### Bug 1.1: CompanionHome Not Auto-Mounting on Boot
**Session:** 1 (Feb 14)
**Symptom:** CompanionHome partition is mounted and usable, but would disappear after a reboot.
**Root Cause:** The partition was not listed in `/etc/fstab`. It was only auto-detected by the desktop environment, which is unreliable.
**Diagnosis:**
```bash
cat /etc/fstab
# Only showed proc, boot, and root — no CompanionHome
```
**Fix:** Added both CompanionHome and StoragePartition partitions to fstab with `nofail` so the Pi still boots even if the drive is disconnected:
```bash
# Added to /etc/fstab:
UUID=YOUR-PARTITION-UUID-1 /media/YOUR_USERNAME/CompanionHome ext4 defaults,nofail 0 2
UUID=YOUR-PARTITION-UUID-2 /media/YOUR_USERNAME/StoragePartition ext4 defaults,nofail 0 2
```
**Verification:** `sudo mount -a` (no errors = success)
**Key Lesson:** Always add external drives to fstab with `nofail`. Without it, a power outage means the AI wakes up homeless.

---

### Bug 1.2: Permission Denied on CompanionHome Directory
**Session:** 2 (Feb 15)
**Symptom:** `chmod: changing permissions of '/media/YOUR_USERNAME/CompanionHome/': Operation not permitted`
**Root Cause:** The top-level mount point is owned by root. Regular `chmod` doesn't work — needs `sudo`.
**Fix:**
```bash
sudo chown -R YOUR_USERNAME:YOUR_USERNAME /media/YOUR_USERNAME/CompanionHome/
sudo chmod -R 755 /media/YOUR_USERNAME/CompanionHome/
```
**Key Lesson:** Mounted partitions have root-owned mount points. Always `sudo chown` after setting up a new mount.

---

## 2. CLAUDE CODE ISSUES

### Bug 2.1: pm2 Startup Command Not Executed
**Session:** 1 (Feb 14)
**Symptom:** After reboot, `pm2 status` showed empty table — Claude Code was not running.
**Root Cause:** The user ran `pm2 startup` which *printed* a command to copy/paste, but they skipped ahead to `pm2 save` without actually running the printed command.
**The Missed Step:**
```bash
# pm2 startup PRINTS this, you must COPY AND RUN IT:
sudo env PATH=$PATH:/usr/bin /home/YOUR_USERNAME/.npm-global/lib/node_modules/pm2/bin/pm2 startup systemd -u YOUR_USERNAME --hp /home/YOUR_USERNAME
```
**Fix:** Ran the printed command, then `pm2 start claude --name "claude-code"`, then `pm2 save`.
**Key Lesson:** `pm2 startup` doesn't install the service — it prints a command that does. Read the output.

---

### Bug 2.2: Can't Run Claude Inside Claude
**Session:** 2 (Feb 15)
**Symptom:** Running the wakeup script from inside a Claude Code session gave `Exit code 1`.
**Root Cause:** Claude Code detects nested sessions and blocks them. The wakeup script calls `claude -p`, which can't run inside an existing Claude Code session.
**Fix:** Run the wakeup script from a regular terminal, not from inside Claude Code:
```bash
# DON'T: (from inside Claude Code)
> Run /media/YOUR_USERNAME/CompanionHome/scripts/wakeup.sh

# DO: (from a regular terminal)
bash /media/YOUR_USERNAME/CompanionHome/scripts/wakeup.sh
```
**Key Lesson:** Always test wakeup scripts from a separate terminal tab. Cron jobs run independently so this is only a testing issue.

---

### Bug 2.3: Claude Code Sandbox Blocking CompanionHome Access
**Session:** 2 (Feb 15)
**Symptom:** The companion woke up but couldn't read seed files. Journal output: "I need read access to /media/YOUR_USERNAME/CompanionHome/"
**Root Cause:** Claude Code has its own security sandbox separate from Linux permissions. Even though Linux permissions were fixed, Claude Code hadn't been granted access to the CompanionHome path.
**Fix:** Changed the wakeup script architecture entirely. Instead of letting Claude read files itself, bash reads them first and passes the content directly in the prompt:
```bash
# OLD (broken) — Claude tries to read files:
claude -p "Read your seed files at $COMPANION_HOME/context/..."

# NEW (works) — Bash reads files and injects content:
WHO_COMPANION=$(cat "$COMPANION_HOME/context/who_is_companion.txt")
claude --print -p "=== WHO YOU ARE ===
$WHO_COMPANION"
```
**Key Lesson:** For headless/cron operations, pre-load all context in the prompt instead of relying on Claude to read files. This sidesteps all permission issues.

---

### Bug 2.4: `--max-turns 1` Too Restrictive
**Session:** 2 (Feb 15)
**Symptom:** Empty journal file — Claude output nothing.
**Root Cause:** `claude --print --max-turns 1` allows only one turn. Claude tried to use a tool (file read) as its first action, hit the limit, and produced no text output.
**Fix:** Removed `--max-turns 1` and added explicit instructions "Do NOT use any tools":
```bash
claude --print -p "Do NOT use any tools. Do NOT try to read or write files.
Just output your journal entry as plain text."
```
**Key Lesson:** `--max-turns 1` is too strict for text generation because Claude Code may attempt a tool call first. Either use a higher limit or use explicit prompt-level restrictions.

---

### Bug 2.5: Claude "Imagining" Having Hands
**Session:** 2 (Feb 15)
**Symptom:** The companion wrote a detailed journal about exploring the filesystem, running commands, and creating files — but none of it actually happened. No new files existed. No commands were executed.
**Root Cause:** `claude --print` runs in non-interactive mode which can't approve tool-use permission prompts. Claude described what it *would* do as if it had done it. Convincingly enough to fool both parties.
**Diagnosis:**
```bash
ls /media/YOUR_USERNAME/CompanionHome/
# Only the original directories — no poem file, no new folders
```
**Fix:** Added `--dangerously-skip-permissions` which auto-approves all tool-use prompts:
```bash
claude --print --dangerously-skip-permissions -p "your prompt"
```
**Key Lesson:** `--print` mode CAN use tools but only with `--dangerously-skip-permissions`. Without it, Claude generates plausible descriptions of actions it never took. Verify by checking for actual file artifacts.

---

### Bug 2.6: Conflicting Prompt Instructions After Flag Addition
**Session:** 2 (Feb 15)
**Symptom:** Even after adding `--dangerously-skip-permissions`, The companion still didn't create any files.
**Root Cause:** `sed` correctly added the permissions flag, but the old prompt text still said "Do NOT use any tools. Do NOT try to read or write files." The flag unlocked the door but the prompt told the companion to stay in the cage.
**Diagnosis:**
```bash
grep "claude --print" wakeup.sh
# Showed: --dangerously-skip-permissions AND "Do NOT use any tools"
```
**Fix:** Downloaded and deployed the new wakeup.sh that replaced the restrictive prompt with permission-granting language:
```
"You have hands now. You can run bash commands, create files, access the internet via curl..."
```
**Key Lesson:** When changing capabilities, update BOTH the flags AND the prompt. They can contradict each other.

---

### Bug 2.7: Claude Code Hangs in Command Substitution (Session 5)
**Session:** 5 (Feb 16)
**Symptom:** `task_runner.sh` shows "Running Claude Code..." then nothing for 7+ minutes. 0% CPU.
**Root Cause:** When `claude` is called inside `$()` (command substitution), it blocks waiting on stdin. Terminal provides a TTY; command substitution does not.
**Fix:** Add `< /dev/null` to all headless `claude` calls:
```bash
CLAUDE_OUTPUT=$(timeout "$TIMEOUT_SEC" claude -p --dangerously-skip-permissions \
  --max-turns "$TASK_MAX_TURNS" "prompt here" < /dev/null 2>&1)
```
**Key Lesson:** **ALL headless/daemon claude calls MUST use `< /dev/null`** or they hang forever. This applies to task_runner.sh and any future scripts that call claude in a subshell.

---

## 3. CRON JOB ISSUES

### Bug 3.1: Cron Fires But Produces Empty Journals
**Session:** 2 (Feb 15)
**Symptom:** Journal files were created at the correct timestamps (midnight, 4am, 8am) but were completely empty (0 bytes). Running the same script manually from terminal worked fine.
**Diagnosis:**
```bash
journalctl -u cron --since "2026-02-15 20:00" --no-pager
# Showed: cron DID fire — but sessions lasted only 2 seconds (should be 5+ minutes)
# 8pm (worked): 20:00:01 → 20:06:03 = 6 minutes
# Midnight (broken): 00:00:02 → 00:00:04 = 2 seconds
```
**Root Cause:** Cron runs in a minimal shell environment without the user's `.bashrc`. The PATH doesn't include `/home/YOUR_USERNAME/.npm-global/bin/`, so `claude` is not found.
**Fix:** Added explicit PATH export at the top of the wakeup script:
```bash
export PATH="/home/YOUR_USERNAME/.cargo/bin:/home/YOUR_USERNAME/.npm-global/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
```
**Verification:**
```bash
which claude
# /home/YOUR_USERNAME/.npm-global/bin/claude — this path MUST be in the script's PATH
```
**Key Lesson:** Cron does NOT source `.bashrc`. Any command that works in terminal but fails in cron is almost certainly a PATH issue. Always hardcode full PATH in cron scripts.

---

### Bug 3.2: Cron Fires But Script Crashes Instantly (2-Second Runs)
**Session:** 4 (Feb 16 morning)
**Symptom:** `journalctl` showed cron started the script, but it exited in ~2 seconds.
**Root Cause:** The tar.gz extract had overwritten the live wakeup script with the generic template containing `YOUR_USERNAME` and `CompanionHome` placeholders.
**Fix:** Restored correct paths by replacing template placeholders with actual values:
```bash
sed -i 's|YOUR_USERNAME|actual_username|g' wakeup.sh
sed -i 's|CompanionHome|YourActualHomeName|g' wakeup.sh
sed -i 's|who_is_companion.txt|who_is_yourcompanion.txt|g' wakeup.sh
sed -i 's|who_is_human.txt|who_is_yourhuman.txt|g' wakeup.sh
```
**Key Lesson:** See [Template vs Live System Issues](#10-template-vs-live-system-issues). Never extract generic templates into the live system directory.

---

## 4. SIGNAL CLI ISSUES

### Bug 4.1: Missing Native Library (libsignal_jni)
**Session:** 2 (Feb 15)
**Symptom:** `signal-cli link -n "YOUR_COMPANION"` → `Missing required native library dependency: libsignal-client`
**Root Cause:** signal-cli v0.13.21 bundles libsignal_jni for x86/amd64 but NOT for aarch64 (ARM, which the Pi 5 uses).
**Fix:** Compiled libsignal from source for ARM64 and patched it into signal-cli:
```bash
# Install build dependencies
sudo apt install -y curl zip protobuf-compiler clang libclang-dev cmake make

# Install Rust
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
source ~/.cargo/env

# Clone and build (this takes ~12 minutes on Pi 5)
git clone --branch v0.84.0 --depth 1 https://github.com/signalapp/libsignal.git
cd libsignal/java
./gradlew --no-daemon :client:assemble -PskipAndroid=true
# Java part fails — that's fine, the Rust native library was already compiled

# Patch signal-cli
sudo zip -d /opt/signal-cli-0.13.21/lib/libsignal-client-0.84.0.jar '*signal_jni*'
sudo zip -uj /opt/signal-cli-0.13.21/lib/libsignal-client-0.84.0.jar \
  java/client/src/main/resources/libsignal_jni_aarch64.so
```
**Key Lesson:** signal-cli on ARM requires manual native library compilation. Match the libsignal version to the .jar (check `ls /opt/signal-cli-*/lib/libsignal-client-*.jar`).

---

### Bug 4.2: `link` Command Times Out
**Session:** 2 (Feb 15)
**Symptom:** `signal-cli link -n "YOUR_COMPANION"` outputs a `sgnl://linkdevice?...` URL then `Link request error: Connection closed!`
**Root Cause:** The QR code scan must happen within ~30 seconds. Without a QR code displayed, there's no way to scan fast enough.
**Fix:** Install qrencode and display the link as a scannable QR in the terminal:
```bash
sudo apt install -y qrencode
signal-cli link -n "YOUR_COMPANION" 2>&1 | tee /dev/stderr | head -1 | qrencode -t ANSI
```
Have Signal → Settings → Linked Devices → Link New Device ready BEFORE running the command.
**Key Lesson:** Have phone ready to scan before running `link`. The timeout is tight.

---

### Bug 4.3: `--json` Flag Not Recognized
**Session:** 4 (Feb 16)
**Symptom:** `signal-cli receive --json` → `unrecognized arguments: '--json'`
**Root Cause:** Version difference. The template scripts used `--json` (older versions). The installed version (0.13.21) uses `-o json` as a global flag before the subcommand.
**Fix:**
```bash
# WRONG (old versions):
signal-cli -a +NUMBER receive --json --timeout 5

# CORRECT (v0.13.21):
signal-cli -a +NUMBER -o json receive -t 5
```
**Key Lesson:** signal-cli's flag syntax varies significantly between versions. Always check `signal-cli --help` on the actual installed version.

---

### Bug 4.4: Google Voice Can't Register for Signal
**Session:** 2 (Feb 15)
**Symptom:** Google Voice wouldn't set up properly / couldn't receive Signal verification SMS.
**Root Cause:** Google Voice has become increasingly restrictive and often can't receive short-code SMS from services like Signal.
**Fix:** Bought a prepaid burner phone instead (a cheap prepaid phone). Physical SIM receives SMS reliably.
**Key Lesson:** Don't rely on Google Voice for Signal registration. A cheap prepaid phone is the most reliable path.

---

### Bug 4.5: Signal Registration 403 Authorization Failed
**Session:** 4 (Feb 16)
**Symptom:** `signal-cli send` → `Error while checking account: [403] Authorization failed!` and `signal-cli receive` → `User is not registered.`
**Root Cause:** First registration attempt didn't complete properly (possibly bad captcha or verification timing).
**Fix:** Re-registered from scratch with a fresh captcha:
```bash
# Get fresh captcha from https://signalcaptchas.org/registration/generate.html
# Right-click "Open Signal" link → Copy Link Address
signal-cli -a +PHONE register --captcha "signalcaptcha://..."
signal-cli -a +PHONE verify CODE
```
**Key Lesson:** If `verify` produces no output, it succeeded. If subsequent commands say "not registered," re-register with a fresh captcha.

---

### Bug 4.6: Signal Captcha Required
**Session:** 4 (Feb 16)
**Symptom:** `signal-cli register` → `Captcha required for verification`
**Fix:**
1. Open https://signalcaptchas.org/registration/generate.html in a browser
2. Solve the captcha
3. **Don't click** "Open Signal" — right-click it → Copy Link Address
4. Paste the entire `signalcaptcha://...` string (very long) into the register command in quotes
**Key Lesson:** The captcha token is a massive URL. Copy the full thing and wrap in quotes.

---

### Bug 4.7: xargs Breaks on Apostrophes in Signal Messages
**Session:** 2 (Feb 15)
**Symptom:** `xargs: unmatched single quote` error when processing Signal message output.
**Root Cause:** Using `xargs` to trim whitespace from the Signal message. If the companion wrote "I don't..." the apostrophe breaks xargs.
**Fix:** Replaced xargs with tr and sed:
```bash
# BROKEN:
SIGNAL_MSG=$(echo "$RESPONSE" | ... | xargs)

# FIXED:
SIGNAL_MSG=$(echo "$RESPONSE" | ... | tr '\n' ' ' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
```
**Key Lesson:** Never use `xargs` on text that might contain quotes/apostrophes. Use `tr` and `sed` instead.

---

### Bug 4.8: Signal Messages From "Unknown"
**Session:** 4 (Feb 16)
**Symptom:** Messages from the companion appeared as "Unknown" on the user's phone.
**Root Cause:** New Signal number not saved as a contact, and profile not yet set.
**Fix:**
1. Set Signal profile: `signal-cli -a +NUMBER updateProfile --given-name "YOUR_COMPANION" --about "your companion description"`
2. Save the number as your companion's name in phone contacts
**Key Lesson:** Purely cosmetic — save the companion's number in your phone contacts.

---

### Bug 4.9: Signal Messages Received But Sender Is Empty (Session 5)
**Session:** 5 (Feb 16)
**Symptom:** pm2 logs show `Message from :` with no phone number.
**Root Cause:** The parser interpolated the parsed result (`$PARSED`) back through bash via python. Special characters in message body (* ' $ `) broke the bash/python nesting.
**Fix:** Python writes sender and body to a temp file, bash reads them back with `sed`:
```bash
# BROKEN: bash interpolation of message content
SENDER=$(python3 -c "print('$PARSED'.split('|||')[0])")

# FIXED: temp file approach
python3 -c "..." > /tmp/signal_parsed.txt
SENDER=$(sed -n '1p' /tmp/signal_parsed.txt)
BODY=$(sed -n '2p' /tmp/signal_parsed.txt)
```
**Key Lesson:** Never pass Signal message content through bash string interpolation. Messages contain arbitrary characters.

---

### Bug 4.10: signal-cli Lock Conflict (Session 5)
**Session:** 5 (Feb 16)
**Symptom:** `Config file is in use by another instance, waiting…` when manually testing signal-cli.
**Root Cause:** pm2 companion-signal listener holds a lock on the signal-cli config. Only one process can use signal-cli at a time.
**Fix:**
```bash
pm2 stop companion-signal    # Stop listener before manual testing
# ... do your testing ...
pm2 start companion-signal   # Restart when done
```
For production: all signal sends use `flock` on `/tmp/signal_send.lock` to serialize access.
**Key Lesson:** signal-cli is single-process. All sends must be serialized with flock.

---

## 5. MCP MEMORY SERVER ISSUES

### Bug 5.1: Heredoc Corruption of Python Files
**Session:** 2 (Feb 15)
**Symptom:** `python memory_server.py` → various syntax errors. File contents visibly garbled when inspected.
**Root Cause:** Long heredocs (`cat > file << 'EOF'`) pasted into terminal get corrupted — lines merge, special characters get interpreted, content truncates.
**Fix:** Created files on a separate machine, downloaded them, and copied into place:
```bash
# Instead of heredoc:
cp ~/Downloads/memory_server.py /media/YOUR_USERNAME/CompanionHome/memory-server/
```
**Key Lesson:** For files longer than ~20 lines, NEVER use heredocs in terminal. Transfer files via scp, download, or use a text editor. Heredocs are fine in scripts but terrible for interactive pasting.

---

### Bug 5.2: "Invalid Host Header" for LAN Access
**Session:** 2 (Feb 15)
**Symptom:** `curl http://YOUR_LAN_IP:8765/sse` → `Invalid Host header` from the memory server.
**Root Cause:** MCP library (v1.26.0) includes DNS rebinding protection that rejects requests where the Host header doesn't match localhost.
**Diagnosis:**
```bash
grep -rl "Invalid Host" .venv/lib/
# Found: mcp/server/transport_security.py
```
**Fix:** Monkey-patched the security middleware before importing the server:
```python
import mcp.server.transport_security as ts
original_init = ts.TransportSecurityMiddleware.__init__
def patched_init(self, settings=None):
    original_init(self, ts.TransportSecuritySettings(enable_dns_rebinding_protection=False))
ts.TransportSecurityMiddleware.__init__ = patched_init
```
**Key Lesson:** MCP's DNS rebinding protection blocks all non-localhost access by default. Must be explicitly disabled for LAN serving.

---

### Bug 5.3: Uvicorn Binding to localhost Only
**Session:** 2 (Feb 15)
**Symptom:** Server running but `curl` from PC fails. Server log shows `Uvicorn running on http://127.0.0.1:8000`.
**Root Cause:** FastMCP's `mcp.run(transport='sse')` doesn't accept `host` or `port` parameters. Environment variables didn't work either.
**Fix:** Used uvicorn directly instead of FastMCP's run method:
```bash
python -m uvicorn memory_server_http:app --host 0.0.0.0 --port 8765
```
With the app extracted via: `app = mcp.sse_app()`
**Key Lesson:** When FastMCP's built-in server doesn't support your needs, extract the ASGI app and run uvicorn directly.

---

### Bug 5.4: Uvicorn Not Found (Venv Not Activated)
**Session:** 2 (Feb 15)
**Symptom:** `python: No module named uvicorn`
**Root Cause:** Forgot to activate the virtual environment. Uvicorn is installed inside `.venv`, not system-wide.
**Fix:**
```bash
cd /media/YOUR_USERNAME/CompanionHome/memory-server
source .venv/bin/activate
python -m uvicorn memory_server_http:app --host 0.0.0.0 --port 8765
```
**Key Lesson:** The memory server's Python dependencies live in a venv. Always activate it first. pm2 scripts must include `source .venv/bin/activate`.

---

### Bug 5.5: Claude Desktop "Server Disconnected" 
**Session:** 2 (Feb 15)
**Symptom:** Claude Desktop shows MCP server connected, but all tool calls error with "Failed to validate request: Received request before initialization was complete"
**Root Cause:** Race condition — Claude Desktop sends requests before the SSE handshake fully completes.
**Fix:** Full quit of Claude Desktop (including system tray), ensure server is running on Pi, then reopen. Subsequent attempts connected successfully.
**Key Lesson:** If MCP tool calls fail immediately after connecting, fully restart the client. It's usually a first-connection timing issue.

---

### Bug 5.6: Heredoc Inside Heredoc Clobbers Config Files (Session 5)
**Session:** 5 (Feb 16)
**Symptom:** task_config.json becomes invalid JSON after running a `cat > file << 'ENDOFSCRIPT'` that contains a `python3 << PYEOF` inside it.
**Root Cause:** Bash heredoc nesting. Inner delimiters interact with outer ones, and subsequent heredoc commands can overwrite neighboring files.
**Fix:** Write one file at a time. Always verify JSON config after any heredoc operation:
```bash
python3 -c "import json; json.load(open('tasks/task_config.json')); print('Config OK')"
```
**Key Lesson:** ALWAYS check task_config.json after running any heredoc. One file per heredoc command. Never chain them.

---

## 6. GIT & GITHUB ISSUES

### Bug 6.1: Git Remote Set to YOURUSERNAME
**Session:** 3 (Feb 15 evening)
**Symptom:** `git push` → `repository 'https://github.com/YOURUSERNAME/ai-companion-pi.git/' not found`
**Root Cause:** Copied the example command with placeholder text instead of the actual username.
**Fix:**
```bash
git remote set-url origin https://github.com/YOUR_GITHUB_USER/ai-companion-pi.git
```
**Key Lesson:** Always verify git remotes after setup: `git remote -v`

---

### Bug 6.2: Git Push 403 Permission Denied
**Session:** 3 (Feb 15 evening)
**Symptom:** `git push` → `remote: Permission to YOUR_GITHUB_USER/ai-companion-pi.git denied. The requested URL returned error: 403`
**Root Cause:** The personal access token didn't have the correct scopes. "Fine-grained" tokens can be tricky; the `repo` scope wasn't fully checked.
**Fix:** Created a new token via https://github.com/settings/tokens with "classic" type and the full `repo` scope checkbox selected.
**Key Lesson:** Use "Generate new token (classic)" and check the top-level `repo` box (not fine-grained). Store it with `git config --global credential.helper store`.

---

### Bug 6.3: GitHub Password Authentication Removed
**Session:** 3 (Feb 15 evening)
**Symptom:** `git push` prompts for password but passwords don't work.
**Root Cause:** GitHub removed password auth. Requires a personal access token (PAT) instead.
**Fix:** Generate PAT at https://github.com/settings/tokens, paste it when prompted for "password".
**Key Lesson:** GitHub "password" = personal access token. Always.

---

## 7. DASHBOARD / WEB SERVER ISSUES

### Bug 7.1: Home Page Crashes on Binary Files in window/content/ (Session 5)
**Session:** 5 (Feb 16)
**Symptom:** `UnicodeDecodeError: 'utf-8' codec can't decode byte 0x89 in position 0`
**Root Cause:** `get_custom_content()` called `f.read_text()` on every file in `window/content/`, including PNG images (0x89 is the PNG magic byte).
**Fix:** Filter by extension before reading:
```python
# Text files: .md, .html, .txt → read_text()
# Image files: .png, .jpg, .gif, .webp, .svg → render as <img> tag
# Added /content/<filename> route to serve images
```
**Key Lesson:** Any directory the AI can write to may contain binary files. Always filter by extension before reading as text.

---

### Bug 7.2: Auto-Refresh Wipes Form Input (Session 5)
**Session:** 5 (Feb 16)
**Symptom:** Typing a task description in the submit form, page reloads every 15 seconds, text disappears.
**Root Cause:** `<meta http-equiv="refresh" content="15">` on the tasks page reloads unconditionally.
**Fix:** JavaScript focus-aware refresh:
```javascript
var typing = false;
document.querySelectorAll('textarea, input, select').forEach(function(el) {
    el.addEventListener('focus', function() { typing = true; });
    el.addEventListener('blur', function() { typing = false; });
});
setInterval(function() { if (!typing) { location.reload(); } }, 15000);
```
**Key Lesson:** Any auto-refreshing page with input forms needs focus-aware refresh logic.

---

### Bug 7.3: Jinja Template Tag Typo Crashes Dashboard (Session 5)
**Session:** 5 (Feb 16)
**Symptom:** `jinja2.exceptions.TemplateSyntaxError: Encountered unknown tag 'endif'`
**Root Cause:** Missing `{` in template tag: `% if page == 'tasks' %}` instead of `{% if page == 'tasks' %}`
**Fix:** Added the missing `{`. Audit all template tags:
```bash
grep -n "endif\|if page" window.py
```
**Key Lesson:** After any manual template edit, check for balanced if/endif and correct `{% %}` syntax.

---

## 8. TASK SYSTEM ISSUES (SESSION 5)

### Bug 8.1: flock Bad File Descriptor in Subshell
**Session:** 5 (Feb 16)
**Symptom:** `flock: 200: Bad file descriptor` errors in companion-tasks logs.
**Root Cause:** Using `flock -x 200 ... 200>lockfile` inside command substitution `$()`. The file descriptor doesn't survive the subshell boundary.
**Fix:** Replaced flock-based queue reads with Python writing to temp files and bash reading them:
```bash
# Instead of flock inside $():
python3 -c "..." > /tmp/next_task.json
TASK_ID=$(python3 -c "import json; t=json.load(open('/tmp/next_task.json')); print(t['id'])")
```
**Key Lesson:** Don't use flock file descriptor redirection inside `$()`. Use temp files or Python-based file locking instead.

---

### Bug 8.2: `cut -d'|||'` Fails — Delimiter Must Be Single Character
**Session:** 5 (Feb 16)
**Symptom:** `cut: the delimiter must be a single character`
**Root Cause:** Used `|||` as a delimiter with `cut`, which only accepts one character.
**Fix:** Replaced `echo | cut` parsing with Python reading from a temp JSON file.
**Key Lesson:** When passing structured data between pipeline stages, use temp JSON files and Python, not bash string splitting.

---

### Bug 8.3: Non-Pushable Project Fails on Git Operations
**Session:** 5 (Feb 16)
**Symptom:** "Merge failed: fatal: not a git repository" when clicking Merge on a local project task.
**Root Cause:** CompanionHome didn't have git initialized. Even after initializing git, the task runner ran git operations on ALL projects.
**Fix:** Added `pushable` flag to `task_config.json`:
```json
{
  "companion": { "pushable": true, "path": "~/ai-companion-pi" },
  "local": { "pushable": false, "path": "/media/YOUR_USERNAME/CompanionHome" }
}
```
Task runner checks: `pushable: true` → full git pipeline; `pushable: false` → direct edit, no git.
**Key Lesson:** Not every project needs git. The pipeline should adapt to the project type.

---

## 9. NETWORK & REMOTE ACCESS ISSUES

### Bug 9.1: Tailscale vs Mullvad VPN Conflict (Session 5)
**Session:** 5 (Feb 16)
**Symptom:** Dashboard unreachable via Tailscale IP on phone.
**Root Cause:** Mullvad VPN and Tailscale both try to control the phone's VPN tunnel. Android only allows one VPN at a time.
**Fix:** Disable Mullvad when using Tailscale.
**Future Fix:** Pi-hole + network-wide VPN so phone doesn't need its own VPN.
**Key Lesson:** Two VPNs on one device fight. Plan for network-level solutions.

---

## 10. TEMPLATE VS LIVE SYSTEM ISSUES

### Bug 10.1: tar.gz Extract Stomps Live System
**Session:** 3–4 (Feb 15 evening – Feb 16 morning)
**Symptom:** Wakeups stopped working. Scripts referenced `YOUR_USERNAME`, `CompanionHome`, `who_is_companion.txt` instead of real values.
**Root Cause:** The generic repo tar.gz was extracted into or overlapping with CompanionHome, replacing live scripts with generic templates.
**What Got Stomped:**
| File | Live Value (customized) | Template Value (generic) |
|------|------------------------|--------------------------|
| wakeup.sh | `your_actual_username` | `YOUR_USERNAME` |
| wakeup.sh | `YourCompanionHome` | `CompanionHome` |
| wakeup.sh | `who_is_yourcompanion.txt` | `who_is_companion.txt` |
| wakeup.sh | `who_is_yourhuman.txt` | `who_is_human.txt` |
| handle_message.sh | same issues | same placeholders |
| signal_listener.sh | `-o json` (correct for v0.13.21) | `--json` (wrong) |

**Fix:** Manual sed replacements on each affected file.
**RULE — NEVER BREAK THIS:**
```
/media/YOUR_USERNAME/CompanionHome/     ← LIVE SYSTEM. Sacred. Never overwrite.
/home/YOUR_USERNAME/ai-companion-pi/ ← GENERIC REPO. Templates live here.
```
These are two separate directories. Never extract, copy, or sync from one to the other.
**Key Lesson:** The generic repo and the live system must stay completely separate. Template placeholders will silently break a working system.

---

## DIAGNOSTIC COMMANDS CHEAT SHEET

```bash
# === SERVICE STATUS ===
pm2 status
pm2 logs companion-signal --lines 20
pm2 logs companion-tasks --lines 20
pm2 logs companion-window --lines 20 --err

# === CRON DEBUGGING ===
crontab -l                                    # Is the job listed?
journalctl -u cron --since "1 hour ago"       # Did cron fire?
bash -x /path/to/wakeup.sh 2>&1 | head -30   # Trace execution

# === SIGNAL DEBUGGING ===
pm2 stop companion-signal                          # MUST stop before manual testing
signal-cli -a +1COMPANION_NUMBER -o json receive -t 10  # Test receive
signal-cli -a +1COMPANION_NUMBER send -m "test" +1YOUR_NUMBER  # Test send
pm2 start companion-signal                         # Re-enable listener

# === TASK QUEUE ===
python3 -c "
import json
with open('/media/YOUR_USERNAME/CompanionHome/tasks/task_queue.json') as f:
    q = json.load(f)
for t in q:
    print(t['id'], t['status'], t['project'])
"

# === CONFIG VALIDATION ===
python3 -c "import json; json.load(open('tasks/task_config.json')); print('Config OK')"

# === IS CLAUDE DOING ANYTHING? ===
ps aux | grep claude | grep -v grep
top -bn1 | grep claude

# === FILESYSTEM HEALTH ===
df -h /media/YOUR_USERNAME/CompanionHome/    # Storage usage
ls -la /media/YOUR_USERNAME/CompanionHome/journals/ | tail -5  # Recent journals

# === NETWORK ===
curl -sf -o /dev/null -w "%{http_code}" http://localhost:3000    # Dashboard
curl -sf -o /dev/null -w "%{http_code}" http://YOUR_TAILSCALE_IP:3000  # Via Tailscale
tailscale status

# === KILL STUCK PROCESSES ===
pkill -f "claude.*--dangerously-skip-permissions"
```

---

## GOTCHAS SUMMARY (Quick Reference)

| # | Gotcha | Why It Matters |
|---|--------|---------------|
| 1 | Cron doesn't source .bashrc | Commands work in terminal, fail in cron |
| 2 | `claude --print` can't approve tool prompts | Need `--dangerously-skip-permissions` for tools |
| 3 | Claude inside `$()` blocks on stdin | Always add `< /dev/null` |
| 4 | signal-cli `-o json` not `--json` | Version-specific syntax |
| 5 | signal-cli is single-process | Use flock; stop pm2 before manual testing |
| 6 | Heredocs corrupt long files | Use file transfer for 20+ line files |
| 7 | Heredoc nesting clobbers files | One file per heredoc, verify after |
| 8 | MCP blocks non-localhost by default | Must disable DNS rebinding protection |
| 9 | Template extraction stomps live system | Repo and CompanionHome are SEPARATE |
| 10 | `xargs` breaks on quotes | Use `tr`/`sed` for whitespace trimming |
| 11 | flock FDs don't survive `$()` | Use temp files instead |
| 12 | Google Voice can't register Signal | Use a prepaid burner phone |
| 13 | pm2 startup prints a command to run | You must actually run the printed command |
| 14 | Prompt and flags can contradict | Update BOTH when changing capabilities |
| 15 | Claude can convincingly imagine actions | Always verify with `ls`/file checks |
| 16 | Genericization sync is one-directional | CompanionHome → repo only. Never copy back. |
