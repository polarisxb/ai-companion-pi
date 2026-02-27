#!/bin/bash
# Start the memory server in HTTP mode for network access
# Run via pm2: pm2 start start_memory_http.sh --name companion-memory --interpreter bash

cd /media/YOUR_USERNAME/CompanionHome/memory-server
source .venv/bin/activate
python -m uvicorn memory_server_http:app --host 0.0.0.0 --port 8765
