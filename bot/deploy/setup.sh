#!/usr/bin/env bash
# Installs or updates the Servi.pe WhatsApp bot on a fresh Ubuntu 24.04 server.
# Run as root:  curl -fsSL https://raw.githubusercontent.com/Servipe01/servi-pe/main/bot/deploy/setup.sh | bash
set -euo pipefail

REPO=https://github.com/Servipe01/servi-pe.git
APP=/opt/servi-pe
ENV_FILE=/etc/servibot/servibot.env
# Public web address of the bot. Default: a free sslip.io name built from the server IP,
# so no DNS setup is needed. To use your own: BOT_HOST=bot.servi.pe bash setup.sh
PUBLIC_IP=$(curl -fsS --max-time 5 http://169.254.169.254/metadata/v1/interfaces/public/0/ipv4/address || hostname -I | awk '{print $1}')
BOT_HOST=${BOT_HOST:-$(echo "$PUBLIC_IP" | tr . -).sslip.io}

echo "==> Installing system packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git python3-venv caddy ufw >/dev/null

echo "==> Firewall: allow SSH, HTTP, HTTPS only"
ufw allow OpenSSH >/dev/null
ufw allow 80/tcp >/dev/null
ufw allow 443/tcp >/dev/null
ufw --force enable >/dev/null

echo "==> Service user and folders"
id servibot >/dev/null 2>&1 || useradd --system --home /var/lib/servibot --shell /usr/sbin/nologin servibot
mkdir -p /var/lib/servibot /etc/servibot
chown servibot:servibot /var/lib/servibot
chmod 750 /var/lib/servibot

echo "==> Getting the code"
if [ -d "$APP/.git" ]; then
  git -C "$APP" pull -q
else
  git clone -q "$REPO" "$APP"
fi
python3 -m venv "$APP/bot/.venv"
"$APP/bot/.venv/bin/pip" install -q --upgrade pip
"$APP/bot/.venv/bin/pip" install -q -r "$APP/bot/requirements.txt"

NEW_SECRETS=0
if [ ! -f "$ENV_FILE" ]; then
  echo "==> Creating settings file with fresh secrets"
  cp "$APP/bot/deploy/servibot.env.example" "$ENV_FILE"
  sed -i "s|^WA_VERIFY_TOKEN=.*|WA_VERIFY_TOKEN=$(openssl rand -hex 16)|" "$ENV_FILE"
  sed -i "s|^SHEETS_SECRET=.*|SHEETS_SECRET=$(openssl rand -hex 24)|" "$ENV_FILE"
  NEW_SECRETS=1
fi
sed -i "s|^PUBLIC_BASE_URL=.*|PUBLIC_BASE_URL=https://$BOT_HOST|" "$ENV_FILE"
chown root:servibot "$ENV_FILE"
chmod 640 "$ENV_FILE"

echo "==> Web server (HTTPS certificate is automatic)"
sed "s|__BOT_HOST__|$BOT_HOST|" "$APP/bot/deploy/Caddyfile" > /etc/caddy/Caddyfile
systemctl reload caddy || systemctl restart caddy

echo "==> Bot service"
cp "$APP/bot/deploy/servibot.service" /etc/systemd/system/servibot.service
systemctl daemon-reload
systemctl enable -q servibot
systemctl restart servibot

echo
echo "Done. Bot status: $(systemctl is-active servibot)"
echo "Bot address:  https://$BOT_HOST"
echo "Meta webhook: https://$BOT_HOST/webhook"
if [ "$NEW_SECRETS" = 1 ]; then
  echo
  echo "Two secrets were generated. You will paste them in two places:"
  grep -E '^(WA_VERIFY_TOKEN|SHEETS_SECRET)=' "$ENV_FILE"
fi
echo
echo "To edit settings:  nano $ENV_FILE   then:  systemctl restart servibot"
echo "To see the log:    journalctl -u servibot -f"
