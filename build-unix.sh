#!/usr/bin/env bash
# Build macOS (.dmg) and Linux (.deb) release packages.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
PLATFORM="${1:-}"
DIST="$ROOT/dist"
PAYLOAD="$DIST/edr-unix"
APP_FILES=(command.py handler.py share.py print.py error.py relay.py guard.py watch.py qrterm.py doctor_checks.py)

if [[ "$PLATFORM" != "macos" && "$PLATFORM" != "linux" ]]; then
  echo "Usage: ./build-unix.sh macos|linux"
  exit 1
fi

VERSION="$(grep -E '^VERSION = ' "$ROOT/print.py" | sed -E 's/^VERSION = "([^"]+)".*/\1/')"
if [[ -z "$VERSION" ]]; then
  VERSION="0.5.5"
fi

# Strip Windows CRLF so CI / macOS / Linux bash does not fail on shebang lines.
fix_crlf() {
  local f="$1"
  if [[ ! -f "$f" ]]; then
    return 0
  fi
  if perl -pi -e 's/\r$//' "$f" 2>/dev/null; then
    return 0
  fi
  sed -i 's/\r$//' "$f" 2>/dev/null || sed -i '' 's/\r$//' "$f"
}
fix_crlf "$ROOT/build-unix.sh"
fix_crlf "$ROOT/scripts/edr"
fix_crlf "$ROOT/scripts/install-macos.sh"
fix_crlf "$ROOT/scripts/install-linux.sh"

echo "Building EDR v${VERSION} payload for $PLATFORM..."
rm -rf "$PAYLOAD"
mkdir -p "$PAYLOAD/app"

for file in "${APP_FILES[@]}"; do
  cp "$ROOT/$file" "$PAYLOAD/app/"
done

install_qrcode_vendor() {
  local app_dir="$1"
  local vendor="$ROOT/build/qr_vendor"
  rm -rf "$vendor"
  mkdir -p "$vendor"
  if python3 -m pip install qrcode -t "$vendor" -q --disable-pip-version-check 2>/dev/null; then
    cp -R "$vendor/qrcode" "$app_dir/qrcode"
  else
    echo "Warning: pip install qrcode failed; terminal QR may be unavailable."
  fi
}
install_qrcode_vendor "$PAYLOAD/app"

cp "$ROOT/scripts/edr" "$PAYLOAD/edr"
chmod +x "$PAYLOAD/edr"

if [[ "$PLATFORM" == "macos" ]]; then
  BUNDLE="$DIST/EDR-Setup-mac"
  DMG_PATH="$DIST/EDR-Setup.dmg"
  DMG_STAGE="$DIST/dmg-stage"

  rm -rf "$BUNDLE" "$DMG_STAGE" "$DMG_PATH"
  mkdir -p "$BUNDLE/edr" "$DMG_STAGE/EDR Project Sharer"

  cp -R "$PAYLOAD/." "$BUNDLE/edr/"
  cp "$ROOT/scripts/install-macos.sh" "$BUNDLE/Install EDR.command"
  cp "$ROOT/installer/INSTALL.txt" "$BUNDLE/README.txt" 2>/dev/null || true
  chmod +x "$BUNDLE/Install EDR.command"

  # DMG layout: app folder + Applications shortcut (standard macOS install UX).
  cp -R "$BUNDLE/." "$DMG_STAGE/EDR Project Sharer/"
  ln -sf /Applications "$DMG_STAGE/Applications"

  hdiutil create \
    -volname "EDR Project Sharer" \
    -srcfolder "$DMG_STAGE" \
    -ov \
    -format UDZO \
    "$DMG_PATH"

  rm -rf "$DMG_STAGE"
  echo "Created: $DMG_PATH"
  echo "Users: open the .dmg, double-click Install EDR.command"
else
  DEB_PATH="$DIST/EDR-Setup.deb"
  DEB_ROOT="$DIST/deb-root"

  rm -rf "$DEB_ROOT" "$DEB_PATH"
  mkdir -p "$DEB_ROOT/DEBIAN" "$DEB_ROOT/usr/share/edr" "$DEB_ROOT/usr/bin"

  cp -R "$PAYLOAD/." "$DEB_ROOT/usr/share/edr/"
  chmod +x "$DEB_ROOT/usr/share/edr/edr"

  cat >"$DEB_ROOT/usr/bin/edr" <<'EOF'
#!/bin/sh
exec /usr/share/edr/edr "$@"
EOF
  chmod 755 "$DEB_ROOT/usr/bin/edr"

  cat >"$DEB_ROOT/DEBIAN/control" <<EOF
Package: edr-project-sharer
Version: ${VERSION}
Section: utils
Priority: optional
Architecture: amd64
Depends: python3
Maintainer: Ender Air Studio
Description: EDR Project Sharer
 Share project folders over LAN or relay. Includes EDR Guard.
Homepage: https://github.com/enderairstudio/Edr
EOF

  cat >"$DEB_ROOT/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
chmod +x /usr/share/edr/edr 2>/dev/null || true
exit 0
EOF
  chmod 755 "$DEB_ROOT/DEBIAN/postinst"

  dpkg-deb --root-owner-group --build "$DEB_ROOT" "$DEB_PATH"
  rm -rf "$DEB_ROOT"

  echo "Created: $DEB_PATH"
  echo "Users: sudo apt install ./EDR-Setup.deb   (or: sudo dpkg -i EDR-Setup.deb)"
fi
