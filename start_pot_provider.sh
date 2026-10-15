#!/bin/bash
# Script khởi động BotGuard PO-Token Provider cho yt-dlp

NODE_BIN="$HOME/.nvm/versions/node/v22.23.3/bin/node"
PROVIDER_DIR="$HOME/bgutil-ytdlp-pot-provider/server"

if [ ! -f "$NODE_BIN" ]; then
    NODE_BIN=$(which node)
fi

echo "[*] Khởi động BotGuard PO-Token Provider trên http://127.0.0.1:4416..."
cd "$PROVIDER_DIR" || exit 1
"$NODE_BIN" build/main.js -p 4416
