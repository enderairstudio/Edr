#!/usr/bin/env bash
# EDR installer for Linux (public release)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
INSTALL_DIR="${EDR_INSTALL_DIR:-$HOME/.local/share/edr}"
BIN_DIR="$HOME/.local/bin"

echo ""
echo "  EDR Project Sharer - Linux Setup"
echo "  Installing to: $INSTALL_DIR"
echo ""

rm -rf "$INSTALL_DIR"
mkdir -p "$INSTALL_DIR" "$BIN_DIR"
cp -R "$ROOT/edr/"* "$INSTALL_DIR/"
chmod +x "$INSTALL_DIR/edr"
ln -sf "$INSTALL_DIR/edr" "$BIN_DIR/edr"

for rc in "$HOME/.bashrc" "$HOME/.zshrc"; do
  if [[ -f "$rc" ]] && ! grep -Fq '.local/bin' "$rc" 2>/dev/null; then
    printf '\n# Local user binaries\nexport PATH="$HOME/.local/bin:$PATH"\n' >>"$rc"
  fi
done

echo "  Done. Open a new shell and run:  edr version"
echo ""
