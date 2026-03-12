#!/usr/bin/env bash
set -e
echo "═══════════════════════════════════════════"
echo " KAPPA ROADMAP — Package Builder"
echo "═══════════════════════════════════════════"
echo

# Check Python
python3 --version >/dev/null 2>&1 || { echo "[ERROR] Python 3 not found."; exit 1; }

# Create .pyz package
echo "Creating portable .pyz package..."
python3 launcher.py --pack

echo
echo "Usage: python3 backups/kappa-roadmap-*.pyz"
