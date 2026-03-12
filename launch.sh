#!/usr/bin/env bash
set -e
if ! command -v python3 &>/dev/null; then
    echo "[*] Python 3 not found. Installing..."
    if command -v apt-get &>/dev/null; then
        sudo apt-get update -qq && sudo apt-get install -y python3 python3-tk
    elif command -v dnf &>/dev/null; then
        sudo dnf install -y python3 python3-tkinter
    elif command -v pacman &>/dev/null; then
        sudo pacman -Sy --noconfirm python tk
    else
        echo "[!] Could not auto-install. Please install Python 3 manually."
        exit 1
    fi
fi
DIR="$(cd "$(dirname "$0")" && pwd)"
PYZ=$(ls -1t "$DIR"/kappa-roadmap-*.pyz 2>/dev/null | head -1)
[ -z "$PYZ" ] && { echo "[!] No .pyz file found."; exit 1; }
echo "[*] Launching $PYZ..."
python3 "$PYZ"
