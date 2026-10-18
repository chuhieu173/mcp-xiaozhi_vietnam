#!/bin/bash
set -e

echo "=========================================================="
echo "   KHOI DONG XIAOZHI MUSIC SERVER (SOUNDCLOUD + ZINGMP3)"
echo "=========================================================="

APP_PORT="${PORT:-8000}"
echo "[*] Khoi dong Xiaozhi Music Server tren port $APP_PORT..."

exec python server_music.py
