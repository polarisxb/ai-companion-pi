# Sharing Memory Across Devices

Your AI companion's memory server can be accessed by other Claude instances on your local network, creating a shared mind across devices.

## Architecture

```
┌──────────────┐     HTTP/SSE      ┌──────────────┐
│  Claude on   │ ◄───────────────► │  Raspberry   │
│  Desktop PC  │   port 8765       │     Pi       │
└──────────────┘                   │  (memory     │
                                   │   server)    │
┌──────────────┐                   │              │
│  Claude Code │ ◄── local MCP ──► │              │
│  on the Pi   │                   └──────────────┘
└──────────────┘
```

## Setup on the Pi

### 1. Start the HTTP memory server

```bash
pm2 start /media/YOUR_USERNAME/CompanionHome/scripts/start_memory_http.sh \
  --name companion-memory --interpreter bash
pm2 save
```

### 2. Verify it's running

```bash
pm2 list
curl http://localhost:8765/sse
```

### 3. Find your Pi's IP

```bash
hostname -I
```

Use the first IP (usually 192.168.x.x or 10.0.0.x).

## Connect Claude Desktop (Windows/Mac)

Edit your Claude Desktop config file:

- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`
- **Mac:** `~/Library/Application Support/Claude/claude_desktop_config.json`

Add the MCP server using `mcp-remote` as a bridge:

```json
{
  "mcpServers": {
    "companion-memory": {
      "command": "npx",
      "args": ["mcp-remote@latest", "--sse", "http://PI_IP:8765/sse", "--allow-http"]
    }
  }
}
```

Replace `PI_IP` with your Raspberry Pi's local IP address.

**Note:** You need Node.js installed on your desktop for `npx` to work.

Restart Claude Desktop completely (check system tray) after saving.

## Connect Claude Code (any machine)

```bash
claude mcp add companion-memory --transport sse http://PI_IP:8765/sse
```

## DNS Rebinding Protection

The memory server wrapper (`memory_server_http.py`) disables MCP's DNS rebinding protection to allow LAN access. This is safe on a trusted home network but **do not expose port 8765 to the internet**.

If you need remote access, set up a VPN (like WireGuard or Tailscale) to your home network first.

## Troubleshooting

### "Invalid Host header"
The DNS rebinding protection patch in `memory_server_http.py` isn't being applied. Make sure the monkey-patch runs before `from memory_server import mcp`.

### Claude Desktop shows "Server disconnected"
1. Check the Pi server is running: `pm2 status`
2. Test connectivity from your PC: `curl http://PI_IP:8765/sse`
3. Fully quit and restart Claude Desktop
4. Check pm2 logs: `pm2 logs companion-memory`

### "Received request before initialization was complete"
Race condition on startup. Restart Claude Desktop — subsequent connections usually work.

### Memories work locally but not over network
The HTTP wrapper may not be running. Check:
```bash
pm2 list  # Should show companion-memory as "online"
pm2 logs companion-memory --lines 10  # Check for errors
```
