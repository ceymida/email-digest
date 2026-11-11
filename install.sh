#!/bin/bash
set -euo pipefail

DIR="$HOME/email-digest"
PLIST="$HOME/Library/LaunchAgents/com.ceydaunal.emaildigest.plist"

if [ ! -f "$DIR/.env" ]; then
    echo "HATA: $DIR/.env bulunamadi. Once .env.example dosyasini .env olarak kopyalayip doldurun."
    exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents"
cp "$DIR/com.ceydaunal.emaildigest.plist" "$PLIST"

launchctl bootout "gui/$(id -u)/com.ceydaunal.emaildigest" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
launchctl enable "gui/$(id -u)/com.ceydaunal.emaildigest"

echo "Kuruldu. Her gun 12:00'de calisacak."
echo "Kontrol: launchctl print gui/$(id -u)/com.ceydaunal.emaildigest"
echo "Hemen test: launchctl kickstart -k gui/$(id -u)/com.ceydaunal.emaildigest"
