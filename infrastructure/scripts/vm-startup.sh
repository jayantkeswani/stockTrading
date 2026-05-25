#!/bin/bash
set -e

MARKER="/opt/stock-trading/.setup-complete"
if [ -f "$MARKER" ]; then
  echo "Setup already complete, skipping."
  exit 0
fi

echo "=== Stock Trading VM Setup ==="

apt-get update -y
apt-get install -y \
  apt-transport-https \
  ca-certificates \
  curl \
  gnupg \
  lsb-release \
  git \
  make \
  jq

echo "Installing Docker..."
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  tee /etc/apt/sources.list.d/docker.list > /dev/null

apt-get update -y
apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

systemctl enable docker
systemctl start docker

echo "Setting up deploy user..."
useradd -m -s /bin/bash deploy 2>/dev/null || true
usermod -aG docker deploy

mkdir -p /home/deploy/.ssh
cp /home/deploy/.ssh/authorized_keys /home/deploy/.ssh/authorized_keys.bak 2>/dev/null || true

DEPLOY_KEY=$(curl -sf -H "Metadata-Flavor: Google" \
  http://metadata.google.internal/computeMetadata/v1/project/attributes/ssh-keys 2>/dev/null || \
  curl -sf -H "Metadata-Flavor: Google" \
  http://metadata.google.internal/computeMetadata/v1/instance/attributes/ssh-keys 2>/dev/null || true)

if [ -n "$DEPLOY_KEY" ]; then
  echo "$DEPLOY_KEY" | grep "^deploy:" | sed 's/^deploy://' > /home/deploy/.ssh/authorized_keys
fi

chown -R deploy:deploy /home/deploy/.ssh
chmod 700 /home/deploy/.ssh
chmod 600 /home/deploy/.ssh/authorized_keys

echo "Creating app directory..."
mkdir -p /opt/stock-trading
chown deploy:deploy /opt/stock-trading

echo "Setting timezone to IST..."
timedatectl set-timezone Asia/Kolkata

echo "Setting up 2GB swap file..."
if [ ! -f /swapfile ]; then
  fallocate -l 2G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
  echo "Swap enabled."
else
  echo "Swap already exists, skipping."
fi

touch "$MARKER"
echo "=== Setup complete ==="
