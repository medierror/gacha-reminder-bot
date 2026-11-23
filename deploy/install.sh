#!/usr/bin/env bash
# Install or update the bot from GitHub, then (re)start it. Run as root; safe to run any time.
set -euo pipefail
BOT_USER=gachabot
HOME_DIR="/home/$BOT_USER"
DIR="$HOME_DIR/gacha-reminder-bot"
REPO="${1:-git@github.com:medierror/gacha-reminder-bot.git}"
as_bot() { (cd / && sudo -H -u "$BOT_USER" "$@"); }

if [ -d "$DIR/.git" ]; then
  echo "==> Downloading the latest version"
  as_bot git -C "$DIR" pull --ff-only
else
  echo "==> Downloading the bot"
  as_bot git clone "$REPO" "$DIR"
fi

echo "==> Installing Python libraries"
[ -x "$DIR/.venv/bin/python" ] || as_bot python3 -m venv "$DIR/.venv"
as_bot "$DIR/.venv/bin/pip" install --quiet --disable-pip-version-check -r "$DIR/requirements.txt"

echo "==> Private folders for the bot's memory and encryption key"
as_bot mkdir -p "$DIR/data" "$HOME_DIR/.config/gacha-reminder-bot"
chmod 700 "$DIR/data" "$HOME_DIR/.config" "$HOME_DIR/.config/gacha-reminder-bot"

install -m 644 "$DIR/deploy/gacha-bot.service" /etc/systemd/system/gacha-bot.service
systemctl daemon-reload

if [ -f "$DIR/.env" ]; then
  chown "$BOT_USER:$BOT_USER" "$DIR/.env"; chmod 600 "$DIR/.env"
  systemctl enable --quiet gacha-bot
  systemctl restart gacha-bot
  sleep 5
  systemctl --no-pager --lines=15 status gacha-bot || true
else
  echo "!! No .env file yet: copy it to $DIR/.env, then run this script again."
fi
