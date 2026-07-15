#!/bin/bash
# Initial VPS provisioning script for Hostinger KVM 4
# Run once on fresh server: bash setup-server.sh
set -e

echo "=== Luminacast Omni — Server Setup ==="

# Update system
apt-get update && apt-get upgrade -y

# Install essentials
apt-get install -y \
    curl wget git vim htop \
    ca-certificates gnupg lsb-release \
    ufw fail2ban \
    ffmpeg

# Install Docker
curl -fsSL https://get.docker.com | sh
systemctl enable docker
systemctl start docker

# Install Docker Compose v2
apt-get install -y docker-compose-plugin

# Create app directory
mkdir -p /opt/luminacast-omni
cd /opt/luminacast-omni

# Configure firewall
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

# Configure fail2ban
systemctl enable fail2ban
systemctl start fail2ban

# Setup certbot for SSL
apt-get install -y certbot

echo ""
echo "=== Server setup complete ==="
echo "Next steps:"
echo "1. Clone repo: git clone <repo-url> /opt/luminacast-omni"
echo "2. Copy .env file to /opt/luminacast-omni/.env"
echo "3. Run: bash infra/scripts/deploy.sh"
echo "4. Setup SSL: certbot certonly --webroot -w /var/www/certbot -d luminacast.com -d www.luminacast.com"
