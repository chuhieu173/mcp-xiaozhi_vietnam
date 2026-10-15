#!/bin/bash
set -e

echo "=========================================================="
echo "   KHOI DONG XIAOZHI MUSIC SERVER TREN RENDER CLOUD"
echo "=========================================================="

# 1. Khoi dong BotGuard PO-Token Provider neu co san
BGUTIL_MAIN="/app/bgutil-server/server/build/main.js"
if [ -f "$BGUTIL_MAIN" ]; then
    echo "[*] Khoi dong BotGuard PO-Token Provider tren port 4416..."
    node "$BGUTIL_MAIN" -p 4416 &
    BG_PID=$!

    # Cho server BotGuard khoi dong
    for i in {1..15}; do
        if curl -s http://127.0.0.1:4416/ping > /dev/null 2>&1; then
            echo "[✓] BotGuard PO-Token Provider da san sang tren port 4416!"
            break
        fi
        sleep 1
    done
fi

# 2. Xac dinh port tu bien moi truong cua Render (mac dinh la 10000 tren Render hoac 8000)
APP_PORT="${PORT:-8000}"
echo "[*] Khoi dong Xiaozhi Music Server tren port $APP_PORT..."

# 3. Chay server music bang Python
exec python server_music.py
