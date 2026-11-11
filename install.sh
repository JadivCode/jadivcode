#!/usr/bin/env bash
# JadivCode installer for Linux.
#
#   Install or update:  curl -fsSL https://raw.githubusercontent.com/JadivCode/jadivcode/main/install.sh | bash
#   Uninstall:          bash install.sh --uninstall
#
# Everything goes into your home folder; sudo is never used.
set -euo pipefail

REPO_URL="${JADIVCODE_REPO:-https://github.com/JadivCode/jadivcode}"
BRANCH="${JADIVCODE_BRANCH:-main}"
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}"
APP_DIR="$DATA_DIR/jadivcode"
BIN_DIR="$HOME/.local/bin"
LAUNCHER="$BIN_DIR/jadivcode"
MIME_FILE="$DATA_DIR/mime/packages/jadivcode.xml"

say() { printf '%s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

uninstall() {
    rm -rf "$APP_DIR"
    rm -f "$LAUNCHER" "$MIME_FILE"
    if command -v update-mime-database >/dev/null 2>&1; then
        update-mime-database "$DATA_DIR/mime" >/dev/null 2>&1 || true
    fi
    say "JadivCode was removed."
}

if [ "${1:-}" = "--uninstall" ]; then
    uninstall
    exit 0
fi

[ "$(uname -s)" = "Linux" ] || die "JadivCode supports Linux only."
command -v python3 >/dev/null 2>&1 || die "python3 is missing. Install it with: sudo apt install python3"
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
    || die "Python 3.10 or newer is needed (found $(python3 --version 2>&1))."

# Use the files next to this script when it runs from a checkout,
# otherwise download the latest version.
SCRIPT_DIR=""
if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

if [ -n "$SCRIPT_DIR" ] && [ -f "$SCRIPT_DIR/jadivcode/__init__.py" ]; then
    say "Installing from $SCRIPT_DIR"
    cp -r "$SCRIPT_DIR/jadivcode" "$WORK/jadivcode"
    [ -d "$SCRIPT_DIR/examples" ] && cp -r "$SCRIPT_DIR/examples" "$WORK/examples"
    [ -f "$SCRIPT_DIR/LICENSE" ] && cp "$SCRIPT_DIR/LICENSE" "$WORK/LICENSE"
else
    command -v curl >/dev/null 2>&1 || die "curl is missing. Install it with: sudo apt install curl"
    command -v tar >/dev/null 2>&1 || die "tar is missing. Install it with: sudo apt install tar"
    say "Downloading JadivCode ($BRANCH)..."
    curl -fsSL "$REPO_URL/archive/refs/heads/$BRANCH.tar.gz" -o "$WORK/src.tar.gz" \
        || die "The download failed. Check your internet connection."
    mkdir "$WORK/src"
    tar -xzf "$WORK/src.tar.gz" -C "$WORK/src" --strip-components=1
    [ -f "$WORK/src/jadivcode/__init__.py" ] || die "The download does not contain JadivCode."
    mv "$WORK/src/jadivcode" "$WORK/jadivcode"
    [ -d "$WORK/src/examples" ] && mv "$WORK/src/examples" "$WORK/examples"
    [ -f "$WORK/src/LICENSE" ] && mv "$WORK/src/LICENSE" "$WORK/LICENSE"
fi
find "$WORK" -name '__pycache__' -type d -prune -exec rm -rf {} +

mkdir -p "$BIN_DIR" "$(dirname "$MIME_FILE")"
rm -rf "$APP_DIR"
mkdir -p "$APP_DIR"
cp -r "$WORK/jadivcode" "$APP_DIR/jadivcode"
[ -d "$WORK/examples" ] && cp -r "$WORK/examples" "$APP_DIR/examples"
[ -f "$WORK/LICENSE" ] && cp "$WORK/LICENSE" "$APP_DIR/LICENSE"

cat > "$LAUNCHER" <<LAUNCH
#!/bin/sh
PYTHONPATH="$APP_DIR\${PYTHONPATH:+:\$PYTHONPATH}" exec python3 -m jadivcode "\$@"
LAUNCH
chmod +x "$LAUNCHER"

cat > "$MIME_FILE" <<'MIME'
<?xml version="1.0" encoding="UTF-8"?>
<mime-info xmlns="http://www.freedesktop.org/standards/shared-mime-info">
  <mime-type type="text/x-jadivcode">
    <comment>JadivCode program</comment>
    <sub-class-of type="text/plain"/>
    <glob pattern="*.jdv"/>
  </mime-type>
</mime-info>
MIME
if command -v update-mime-database >/dev/null 2>&1; then
    update-mime-database "$DATA_DIR/mime" >/dev/null 2>&1 || true
fi

say ""
say "$("$LAUNCHER" --version) is installed."
python3 -c 'import tkinter' >/dev/null 2>&1 \
    || say "Tip: for GUI windows install Tk:   sudo apt install python3-tk"
case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) say "Add $BIN_DIR to your PATH, then open a new terminal:"
       say "    echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.bashrc" ;;
esac
say "Try it:  jadivcode run $APP_DIR/examples/hello.jdv"
