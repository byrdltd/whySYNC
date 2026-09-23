#!/bin/sh
# Installs whySYNC for the current user: a private venv, ~/.local/bin/whysync,
# the systemd user service, and a menu entry with icons.
#
# A copy is installed (not editable) so the service starts even when the
# repository sits on a disk that is not mounted. The venv sees system site
# packages because the window uses the distribution's PyGObject, GTK 4 and
# libadwaita; the service itself needs nothing beyond the standard library.
set -eu

ROOT=$(cd "$(dirname "$0")" && pwd)
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
VENV="$DATA/whysync/venv"
BIN="$HOME/.local/bin/whysync"
APP_ID=com.github.byrdltd.whysync

for tool in rsync python3 systemctl; do
    command -v "$tool" >/dev/null 2>&1 || { echo "install: '$tool' not found" >&2; exit 1; }
done
python3 -c 'import gi; gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")' 2>/dev/null \
    || echo "install: GTK 4 / libadwaita for Python not found; the service works, the window will not." >&2

# Older installs went through `uv tool`; drop that copy so the two do not fight over the binary.
if command -v uv >/dev/null 2>&1 && uv tool list 2>/dev/null | grep -q '^whysync '; then
    uv tool uninstall whysync >/dev/null
fi

rm -rf "$VENV"
python3 -m venv --system-site-packages "$VENV"
"$VENV/bin/pip" install --quiet --disable-pip-version-check "$ROOT"
mkdir -p "$(dirname "$BIN")"
ln -sf "$VENV/bin/whysync" "$BIN"

for n in 16 32 48 64 128 256 512; do
    install -Dm644 "$ROOT/assets/icon-$n.png" "$DATA/icons/hicolor/${n}x${n}/apps/$APP_ID.png"
done
mkdir -p "$DATA/applications"
sed "s|@BIN@|$BIN|" "$ROOT/data/$APP_ID.desktop.in" > "$DATA/applications/$APP_ID.desktop.tmp"
mv "$DATA/applications/$APP_ID.desktop.tmp" "$DATA/applications/$APP_ID.desktop"
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$DATA/applications" || true
command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -q -t "$DATA/icons/hicolor" || true

install -Dm644 "$ROOT/systemd/whysync.service" "$UNIT_DIR/whysync.service"
systemctl --user daemon-reload
systemctl --user enable whysync.service
systemctl --user restart whysync.service
systemctl --user --no-pager status whysync.service | head -5
