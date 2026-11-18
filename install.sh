#!/usr/bin/env bash
# 不带参数：把 Clawd 加进应用菜单。带参数就只做参数说的事：
#   --autostart  登录桌面时它自己出现
#   --windows    装上 GNOME 扩展，让它能在窗口上走（装完要注销重新登录一次）
# 撤销：rm ~/.local/share/applications/clawd-pet.desktop ~/.config/autostart/clawd-pet.desktop
#       gnome-extensions uninstall clawd-windows@clawd.pet
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
AUTOSTART="${XDG_CONFIG_HOME:-$HOME/.config}/autostart"
UUID="clawd-windows@clawd.pet"
EXTENSIONS="${XDG_DATA_HOME:-$HOME/.local/share}/gnome-shell/extensions"

entry() {
  cat <<EOF
[Desktop Entry]
Type=Application
Name=Clawd 桌宠
Comment=住在桌面底边的小 Clawd
Exec=python3 "$DIR/clawd.py"
Icon=$DIR/icon.png
Terminal=false
Categories=Game;Amusement;
EOF
}

if [[ $# -eq 0 ]]; then
  mkdir -p "$APPS"
  entry > "$APPS/clawd-pet.desktop"
  echo "已加入应用菜单：$APPS/clawd-pet.desktop"
fi

for arg in "$@"; do
  case "$arg" in
    --autostart)
      mkdir -p "$AUTOSTART"
      entry > "$AUTOSTART/clawd-pet.desktop"
      echo "已设置开机自启：$AUTOSTART/clawd-pet.desktop"
      ;;
    --windows)
      mkdir -p "$EXTENSIONS/$UUID"
      cp "$DIR/gnome-extension/$UUID/metadata.json" "$DIR/gnome-extension/$UUID/extension.js" "$EXTENSIONS/$UUID/"
      # Wayland 下 GNOME Shell 只在登录时扫描扩展，所以直接写进启用列表，下次登录生效
      python3 - "$UUID" <<'EOF'
import sys
from gi.repository import Gio
settings = Gio.Settings.new("org.gnome.shell")
enabled = settings.get_strv("enabled-extensions")
if sys.argv[1] not in enabled:
    settings.set_strv("enabled-extensions", enabled + [sys.argv[1]])
Gio.Settings.sync()
EOF
      echo "已安装并启用 GNOME 扩展：$EXTENSIONS/$UUID"
      echo "注销后重新登录一次，Clawd 就能在窗口上走了"
      ;;
    *)
      echo "不认识的参数：$arg" >&2
      exit 1
      ;;
  esac
done
