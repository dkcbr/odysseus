#!/bin/bash

set -e

BASE="$HOME/odysseus"
SRC="$BASE/mcp/servers"
DEST="$BASE/mcp_servers"

echo "=== Setting up MCP servers (Auto-Generate Mode) ==="

mkdir -p "$DEST"

SERVERS=("browser" "file" "system" "traderdev" "tradingview")

for S in "${SERVERS[@]}"; do
    echo "[+] Installing MCP server: $S"

    mkdir -p "$DEST/$S"

    # Copy manifest.json
    if [ -f "$SRC/$S/manifest.json" ]; then
        cp "$SRC/$S/manifest.json" "$DEST/$S/"
        echo "    [ok] manifest.json copied"
    else
        echo "    [!] manifest.json missing for $S"
    fi

    # Always generate server.py (safe default)
    echo "    [*] Generating server.py for $S"

    cat <<EOF > "$DEST/$S/server.py"
import json

def handle_ping(args):
    return {"response": f"Pong: {args.get('message', '')}"}

def handle_default(args):
    return {"response": "OK"}

TOOLS = {
    "ping": handle_ping,
}

def handle_request(tool_name, args):
    handler = TOOLS.get(tool_name, handle_default)
    return handler(args)
EOF

    echo "    [ok] server.py created"
done

echo "=== MCP setup complete ==="
echo "Your MCP servers are now located in: $DEST"
