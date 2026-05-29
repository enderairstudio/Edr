#!/usr/bin/env bash
# Build macOS / Linux release packages (EDR-Setup-mac / EDR-Setup-linux).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
PLATFORM="${1:-}"
DIST="$ROOT/dist"
PAYLOAD="$DIST/edr-unix"
APP_FILES=(command.py handler.py share.py print.py error.py relay.py guard.py)

if [[ "$PLATFORM" != "macos" && "$PLATFORM" != "linux" ]]; then
  echo "Usage: ./build-unix.sh macos|linux"
  exit 1
fi

echo "Building EDR payload for $PLATFORM..."
rm -rf "$PAYLOAD"
mkdir -p "$PAYLOAD/app"

for file in "${APP_FILES[@]}"; do
  cp "$ROOT/$file" "$PAYLOAD/app/"
done
cp "$ROOT/scripts/edr" "$PAYLOAD/edr"
chmod +x "$PAYLOAD/edr"

if [[ "$PLATFORM" == "macos" ]]; then
  BUNDLE="$DIST/EDR-Setup-mac"
  rm -rf "$BUNDLE"
  mkdir -p "$BUNDLE"
  cp -R "$PAYLOAD/"* "$BUNDLE/edr/"
  cp "$ROOT/scripts/install-macos.sh" "$BUNDLE/Install-EDR.command"
  cp "$ROOT/installer/INSTALL.txt" "$BUNDLE/" 2>/dev/null || true
  chmod +x "$BUNDLE/Install-EDR.command"
  (cd "$DIST" && tar -czf "EDR-Setup-mac.tar.gz" "EDR-Setup-mac")
  echo "Created: $DIST/EDR-Setup-mac.tar.gz"
  echo "Users: extract, double-click Install-EDR.command"
else
  BUNDLE="$DIST/EDR-Setup-linux"
  rm -rf "$BUNDLE"
  mkdir -p "$BUNDLE"
  cp -R "$PAYLOAD/"* "$BUNDLE/edr/"
  cp "$ROOT/scripts/install-linux.sh" "$BUNDLE/install-edr.sh"
  cp "$ROOT/installer/INSTALL.txt" "$BUNDLE/" 2>/dev/null || true
  chmod +x "$BUNDLE/install-edr.sh"
  (cd "$DIST" && tar -czf "EDR-Setup-linux.tar.gz" "EDR-Setup-linux")
  echo "Created: $DIST/EDR-Setup-linux.tar.gz"
  echo "Users: extract, run ./install-edr.sh"
fi
