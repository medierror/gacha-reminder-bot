#!/usr/bin/env bash
# One-time setup + security hardening of a fresh Ubuntu/Debian server for the bot. Run as root.
set -euo pipefail
BOT_USER=gachabot
export DEBIAN_FRONTEND=noninteractive

echo "==> Updating the system"
apt-get update -q
apt-get -y -q -o Dpkg::Options::=--force-confold upgrade
apt-get -y -q install python3 python3-venv python3-pip git ufw fail2ban unattended-upgrades sudo

echo "==> Automatic security updates (installed every day)"
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'CONF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
CONF

echo "==> Firewall: nothing can connect in except SSH (the bot only makes outgoing connections)"
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw --force enable

echo "==> SSH: key login only, passwords disabled"
# 00- so it wins over cloud-init's 50-cloud-init.conf (sshd uses the first value it reads)
cat > /etc/ssh/sshd_config.d/00-gacha-hardening.conf <<'CONF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin prohibit-password
CONF
sshd -t
systemctl reload ssh 2>/dev/null || systemctl reload sshd

echo "==> fail2ban: bans IPs that try to guess SSH logins"
systemctl enable --now fail2ban

echo "==> Bot user: no password, no login shell, no admin rights"
id "$BOT_USER" >/dev/null 2>&1 || useradd --create-home --shell /usr/sbin/nologin "$BOT_USER"
chmod 700 "/home/$BOT_USER"

echo "==> Read-only GitHub deploy key (lets the server download the private repo, nothing else)"
SSH_DIR="/home/$BOT_USER/.ssh"
mkdir -p "$SSH_DIR"
[ -f "$SSH_DIR/github_deploy" ] || ssh-keygen -q -t ed25519 -N "" -C "gacha-bot-server" -f "$SSH_DIR/github_deploy"
cat > "$SSH_DIR/config" <<'CONF'
Host github.com
  IdentityFile ~/.ssh/github_deploy
  IdentitiesOnly yes
  StrictHostKeyChecking yes
CONF
# GitHub's published SSH host key (https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints)
echo "github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl" > "$SSH_DIR/known_hosts"
chown -R "$BOT_USER:$BOT_USER" "$SSH_DIR"
chmod 700 "$SSH_DIR"; chmod 600 "$SSH_DIR"/*; chmod 644 "$SSH_DIR/github_deploy.pub"

echo "==> Done. Deploy key to add to GitHub (read-only):"
echo "DEPLOY_KEY $(cat "$SSH_DIR/github_deploy.pub")"
