#!/usr/bin/env python3
"""Memory MCP Server - HTTP/SSE mode for network access.

This wrapper disables DNS rebinding protection so the memory server
can be accessed from other devices on the local network.

Only use this on a trusted LAN — do not expose to the internet.
"""
import sys
sys.path.insert(0, '/media/YOUR_USERNAME/CompanionHome/memory-server')

# Disable DNS rebinding protection before anything else loads
import mcp.server.transport_security as ts
original_init = ts.TransportSecurityMiddleware.__init__
def patched_init(self, settings=None):
    original_init(self, ts.TransportSecuritySettings(enable_dns_rebinding_protection=False))
ts.TransportSecurityMiddleware.__init__ = patched_init

from memory_server import mcp
app = mcp.sse_app()
