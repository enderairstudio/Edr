#!/usr/bin/env bash
# EDR installer for macOS (public release)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
INSTALL_DIR="${EDR_INSTALL_DIR:-$HOME/.local/share/edr}"
SHELL_FILE="$HOME/.zprofile"

echo ""
echo "  EDR Project Sharer - macOS Setup"
echo "  Installing to: $INSTALL_DIR"
echo ""

rm -rf "$INSTALL_DIR"
mkdir -p "$INSTALL_DIR"
cp -R "$ROOT/edr/"* "$INSTALL_DIR/"
chmod +x "$INSTALL_DIR/edr"

LINE="export PATH=\"$INSTALL_DIR:\$PATH\""
if [[ -f "$SHELL_FILE" ]] && ! grep -Fq "$INSTALL_DIR" "$SHELL_FILE" 2>/dev/null; then
  printf '\n# EDR Project Sharer\n%s\n' "$LINE" >>"$SHELL_FILE"
elif [[ ! -f "$SHELL_FILE" ]]; then
  printf '# EDR Project Sharer\n%s\n' "$LINE" >"$SHELL_FILE"
fi

echo "  Done. Open a new terminal and run:  edr version"
echo ""
