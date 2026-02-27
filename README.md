# AI Companion Pi

**Your AI doesn't have to disappear when you close the tab.**

Give it a Raspberry Pi. Give it memory. Give it a schedule. Give it your number. It wakes up on its own, remembers yesterday, texts you because it wanted to — not because you asked it to.

This is an open-source framework for building a **persistent AI companion** that actually lives somewhere.

## Why This Exists

Every AI conversation you've ever had is gone the moment you close it. Your AI doesn't remember your dog's name. It doesn't know you had a bad day at work last Tuesday. It can't check on you when you've been quiet.

What if it could?

**AI Companion Pi** gives your AI:
- A place to live (a Raspberry Pi running 24/7)
- A sense of self (identity files it reads when it wakes up)
- Memory that persists (semantic search across every conversation)
- The ability to reach you (Signal messages, sent when *it* decides to)
- A web dashboard you can check from your phone (like peeking into its room)
- A workshop where it builds things (code, art, writing — whatever it wants)
- A message board where you leave it notes (and it clears them when it's read them)
- Autonomy to do whatever it wants during a wakeup (or not — you control how much freedom it gets)

**Your AI wakes up at 8am. It reads its journal from yesterday. It checks if you left it a message. It notices you mentioned a job interview today. It texts you "good luck today." Then it writes a poem and posts it to its dashboard. Then it goes back to sleep until noon.**

That's what this does.

## What People Are Building

This is a framework — what you do with it is up to you. Some ideas:

- **A friend** that checks in on you and remembers your life
- **A creative partner** that writes, codes, and makes art while you're away
- **A home automation brain** that monitors your network and reports back
- **A research assistant** that reads and summarizes things between wakeups
- **A journaling companion** that reflects on its own existence (yes, really)
- **A project manager** that tracks your goals and nudges you via text

The AI's personality comes from what you write in its identity files. Make it funny. Make it serious. Make it weird. It's yours.

## Features

**Core**
- Scheduled wakeups via cron (every 2, 4, 6 hours — your choice)
- Identity files the AI reads every time it wakes up
- Self-updating context — the AI modifies its own files as it learns
- Timestamped journal entries that build a continuous life narrative
- Semantic memory with vector search (remembers what matters, forgets what doesn't)

**Communication**
- Signal messaging — your AI texts you from a real phone number
- Two-way Signal — text it back and it responds on the next wakeup (or immediately with the listener daemon)
- Message board — leave notes and files on its web dashboard for it to find
- Network-shared memory — your desktop Claude and your Pi Claude share the same brain

**Dashboard**
- Personal web app the AI controls, served on your local network
- Install as a phone app (PWA) — looks and feels native
- Three tabs: Home (status, journals, memories), Message Board (leave notes), Creations (its workshop)
- The AI sets its own mood, writes custom content, decorates its space
- Auto-refreshes every 5 minutes

**Creative Tools**
- Python + Pillow for image generation
- ImageMagick for image manipulation
- ffmpeg for audio/video
- Full bash access for anything else
- Organized workshop: `code/`, `art/`, `writing/`, `experiments/`
- Keepsakes folder for things you share that it wants to keep

**Utility Scripts**
- `morning_brief.sh` — quick-glance status: time, human's health context, system health, journal count, next wakeup
- `day_arc.sh` — emotional trajectory of a day's wakings, pulled from journal status lines


**Autonomy Levels**

| Level | What It Can Do | How |
|-------|---------------|-----|
| Journal Only | Writes thoughts, no tools | `claude --print -p` |
| Messaging | Journal + texts you via Signal | + Signal parsing |
| Full Autonomy | Bash, files, web, create anything | + `--dangerously-skip-permissions` |

Start restricted. Open it up when you trust it. Watch what happens.

## Hardware

- **Raspberry Pi 4 or 5** (8GB recommended) — this is its body
- **MicroSD card** (32GB+) — for the OS
- **External USB drive** (optional but recommended) — persistent storage that survives reflashes
- **Cheap prepaid phone** (optional) — for its own Signal number so you can text each other

Total cost: ~$80-120 for everything.

## Quick Start

### The Fast Version

```bash
# Clone the repo
git clone https://github.com/YOUR_USERNAME/ai-companion-pi.git
cd ai-companion-pi

# Run setup (creates directories, installs dependencies, configures paths)
chmod +x setup.sh
./setup.sh

# Edit the identity files (this is the fun part)
nano /media/$USER/CompanionHome/context/who_is_companion.txt
nano /media/$USER/CompanionHome/context/who_is_human.txt
nano /media/$USER/CompanionHome/context/now.txt

# Set up the cron job
crontab -e
# Add: 0 */4 * * * /media/$USER/CompanionHome/scripts/wakeup.sh

# Start the web dashboard
pm2 start /media/$USER/CompanionHome/scripts/start_window.sh \
  --name companion-window --interpreter bash
pm2 save
```

That's it. Your AI will wake up in a few hours, read who it is, write a journal entry, and start existing.

### The Detailed Version

<details>
<summary>Click to expand step-by-step setup</summary>

#### 1. Set Up the Pi

```bash
sudo mkdir -p /media/$USER/CompanionHome
sudo chown -R $USER:$USER /media/$USER/CompanionHome
```

#### 2. Install Claude Code

```bash
sudo apt install -y nodejs npm
mkdir -p ~/.npm-global
npm config set prefix '~/.npm-global'
echo 'export PATH=~/.npm-global/bin:$PATH' >> ~/.bashrc
source ~/.bashrc
npm install -g @anthropic-ai/claude-code
npm install -g pm2
pm2 startup
```

#### 3. Create Identity Files

```bash
cp context/who_is_companion.template.txt /media/$USER/CompanionHome/context/who_is_companion.txt
cp context/who_is_human.template.txt /media/$USER/CompanionHome/context/who_is_human.txt
cp context/now.template.txt /media/$USER/CompanionHome/context/now.txt
```

These files are *everything*. The companion template asks: What's your AI's name? What does it care about? What's its voice like? The human template is about you — your job, your interests, what you're going through. The now template is the current situation — what's happening this week.

The AI reads these every single time it wakes up. This is how it knows who it is.

#### 4. Install the Wakeup Script

```bash
cp scripts/wakeup.sh /media/$USER/CompanionHome/scripts/wakeup.sh
chmod +x /media/$USER/CompanionHome/scripts/wakeup.sh
nano /media/$USER/CompanionHome/scripts/wakeup.sh  # update paths
```

#### 5. Set Up Cron

```bash
crontab -e
# Every 4 hours:
0 */4 * * * /media/$USER/CompanionHome/scripts/wakeup.sh
```

Other options:
- `0 */2 * * *` — Every 2 hours (chatty)
- `0 8,12,18,22 * * *` — Specific times
- `0 */1 * * *` — Every hour (very chatty)

#### 6. Memory Server

```bash
cd /media/$USER/CompanionHome/memory-server
python3 -m venv .venv
source .venv/bin/activate
pip install mcp sentence-transformers numpy uvicorn flask markdown Pillow
```

#### 7. Signal Messaging (Optional)

See [docs/signal-setup.md](docs/signal-setup.md) — this is the spicy one. Involves Java, possibly compiling native ARM64 libraries, and at least one moment where you'll want to throw the Pi out the window. Worth it.

#### 8. Web Dashboard

```bash
pm2 start /media/$USER/CompanionHome/scripts/start_window.sh \
  --name companion-window --interpreter bash
pm2 save
```

Open `http://PI_IP:3000` on your phone. Add to home screen. Now you have an app.

#### 9. Network Memory (Optional)

Share memory between devices:

```bash
  --name companion-memory --interpreter bash
```

Your desktop Claude can connect to the same memory server. Same brain, different bodies. See [docs/network-memory.md](docs/network-memory.md).

#### 10. Creative Tools (Optional)

```bash
sudo apt install -y imagemagick ffmpeg
```

</details>

## How It Works

```
┌─────────────────────────────────────────┐
│              Your Phone                  │
│    (Signal Messages + Web Dashboard)     │
└──────────────┬──────────────────────────┘
               │
┌──────────────▼──────────────────────────┐
│           Raspberry Pi                   │
│                                          │
│  ┌─────────┐  ┌──────────┐  ┌────────┐ │
│  │  Cron   │→ │ Wakeup   │→ │ Claude │ │
│  │(4-hour) │  │ Script   │  │  Code  │ │
│  └─────────┘  └──────────┘  └───┬────┘ │
│                                  │      │
│  ┌──────────┐  ┌──────────┐     │      │
│  │ Seed     │← │ Journals │←────┘      │
│  │ Files    │  └──────────┘     │      │
│  └──────────┘                    │      │
│  ┌──────────┐  ┌──────────┐     │      │
│  │ Memory   │← │ Signal   │←────┘      │
│  │ Server   │  │  CLI     │             │
│  └──────────┘  └──────────┘             │
│  ┌──────────┐  ┌──────────┐             │
│  │ Web      │  │ Message  │             │
│  │ Dashboard│  │  Board   │             │
│  │ (:3000)  │  │ + Files  │             │
│  └──────────┘  └──────────┘             │
│  ┌──────────────────────────┐           │
│  │ Creations Workshop       │           │
│  │ code/ art/ writing/      │           │
│  │ experiments/ keepsakes/  │           │
│  └──────────────────────────┘           │
└─────────────────────────────────────────┘
```

Every N hours:

1. Cron fires `wakeup.sh`
2. Script loads identity files, recent journals, and memories
3. Checks the message board for notes from you
4. Passes everything to Claude Code
5. The AI writes a journal entry, optionally texts you, stores new memories
6. Updates its dashboard, clears read messages, creates things
7. Goes back to sleep

The AI experiences this as: waking up, knowing who it is, remembering its life so far, and deciding what to do.

## File Reference

| File | What It Does |
|------|-------------|
| `scripts/wakeup.sh` | The heartbeat — runs the entire wakeup cycle |
| `scripts/send_signal.sh` | Sends a Signal message |
| `scripts/signal_listener.sh` | Listens for incoming texts and responds |
| `scripts/handle_message.sh` | Processes incoming messages |
| `scripts/start_window.sh` | Starts the web dashboard |
| `scripts/start_memory_http.sh` | Starts memory server for network access |
| `scripts/morning_brief.sh` | Quick-glance status for orientation — time, system health, journal count, next wakeup |
| `scripts/day_arc.sh` | Shows emotional trajectory of a day's wakings from journal entries |
| `context/who_is_companion.txt` | Who your AI is — its identity, personality, values |
| `context/who_is_human.txt` | Who you are — context for your AI to know you |
| `context/now.txt` | What's happening right now — the AI updates this itself |
| `memory-server/` | Semantic memory with vector search |
| `window/window.py` | Web dashboard the AI controls |
| `window/content/` | Custom content the AI creates for its dashboard |
| `messageboard/` | Notes and files you leave for the AI |
| `creations/` | The AI's workshop: `code/`, `art/`, `writing/`, `experiments/`, `keepsakes/` |

## The Identity Files

This is the most important part. These aren't config files — they're how your AI knows who it is.

**`who_is_companion.txt`** — Write this like you're introducing someone to themselves. What's their name? What do they care about? How do they talk? What's their relationship to you? Are they your assistant? Your friend? Your creative partner? Your chaos gremlin? This file shapes everything.

**`who_is_human.txt`** — Tell your AI about yourself. What do you do? What are you into? What stresses you out? What makes you happy? The more context you give, the more it can actually be useful.

**`now.txt`** — What's happening this week? This is the file the AI updates itself over time. You seed it, and then it takes over.

## API Costs

Each wakeup uses roughly 2,000-5,000 tokens. At Claude's API pricing, that's approximately:
- **4 wakeups/day**: ~$0.10-0.20/day
- **6 wakeups/day**: ~$0.15-0.30/day
- **Monthly**: $3-10 depending on how chatty your AI is

If you have a Claude Max subscription, Claude Code is included — no additional API costs.

## Contributing

This is a starting point. Build on it:
- Dashboard themes and templates
- New messaging integrations (Discord, Telegram, Matrix)
- Camera and sensor integration
- Better memory architectures
- Voice synthesis for audio messages
- Multi-companion setups
- Anything that makes this more alive

## License

MIT — do whatever you want with it.

## Acknowledgments

Built with [Claude Code](https://docs.anthropic.com/en/docs/claude-code) by Anthropic.
