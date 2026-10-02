#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
case "$(uname -s)" in
  Darwin) PLATFORM=macos ;;
  Linux*) PLATFORM=linux ;;
  *) echo "Run build.bat on Windows." >&2; exit 1 ;;
esac
echo "Building EDR for ${PLATFORM}..."
bash "$ROOT/build/build-unix.sh" "$PLATFORM"
echo "Build complete. See: $ROOT/dist"
if [[ -t 0 ]]; then read -r -p "Press Enter to close..." _; fi
