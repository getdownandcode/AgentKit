# AgentKit AWS EC2 Deployment Guide

This guide provides step-by-step instructions for deploying a production-ready **AgentKit** instance on a single AWS EC2 virtual machine using Docker Compose.

---

## Table of Contents
1. [Target Architecture](#1-target-architecture)
2. [EC2 Instance Sizing & Provisioning](#2-ec2-instance-sizing--provisioning)
3. [AWS Security Group Configuration](#3-aws-security-group-configuration)
4. [Host Setup & Docker Installation](#4-host-setup--docker-installation)
5. [Storage & Volume Persistence](#5-storage--volume-persistence)
6. [Environment & Secrets Management](#6-environment--secrets-management)
7. [Systemd Service Daemon](#7-systemd-service-daemon)
8. [Reverse Proxy & Automatic SSL Termination](#8-reverse-proxy--automatic-ssl-termination)
9. [Deployment & Verification Checklist](#9-deployment--verification-checklist)
10. [Monitoring, Backups & Maintenance](#10-monitoring-backups--maintenance)

---

## 1. Target Architecture

```
                             Internet
                                │
                                ▼
                       [AWS Security Group]
                   (Ports 80 & 443 Public; 22 Restricted)
                                │
                                ▼
                 ┌─────────────────────────────┐
                 │       EC2 Host (Ubuntu)     │
                 │                             │
                 │   [Reverse Proxy: Caddy]    │
                 │     (Auto Let's Encrypt)    │
                 │              │              │
                 │              ▼              │
                 │     [AgentKit API] (:8000)  │
                 │      (FastAPI + Uvicorn)    │
                 │        │            │       │
                 │        ▼            ▼       │
                 │   [PostgreSQL]   [Redis]    │
                 │     (:5432)       (:6379)   │
                 │   (Internal)    (Internal)  │
                 └─────────────────────────────┘
```

The single-node EC2 architecture runs the complete containerized stack:
- **Reverse Proxy**: Caddy or Nginx handling automatic TLS / HTTPS termination on ports 80/443.
- **AgentKit Application**: FastAPI backend exposed on internal port `8000`.
- **Database**: PostgreSQL 16 Alpine container with attached persistent EBS volume.
- **Cache & Rate Limiting**: Redis 7 Alpine container for session memory and rate limits.
- **Internal Network**: Docker bridge network (`agentkit_net`) isolating PostgreSQL and Redis from public exposure.

---

## 2. EC2 Instance Sizing & Provisioning

### Recommended Hardware Specs
- **Development / Staging**: `t3.small` (2 vCPU, 2 GiB RAM)
- **Production Minimum**: `t3.medium` (2 vCPU, 4 GiB RAM)
- **High Throughput**: `c6i.large` or `c7g.large` (2 vCPU, 4 GiB RAM, Compute-optimized)
- **Root Volume**: Minimum 30 GiB gp3 EBS volume (3000 IOPS, 125 MB/s throughput baseline).

### Provisioning Steps (AWS CLI)
```bash
# Launch Ubuntu 24.04 LTS instance
aws ec2 run-instances \
    --image-id ami-04b70fa74e45c3917 \
    --instance-type t3.medium \
    --key-name your-ec2-keypair \
    --security-group-ids sg-0123456789abcdef0 \
    --subnet-id subnet-0123456789abcdef0 \
    --block-device-mappings '[{"DeviceName":"/dev/sda1","Ebs":{"VolumeSize":30,"VolumeType":"gp3","DeleteOnTermination":false}}]' \
    --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=agentkit-production}]'
```

---

## 3. AWS Security Group Configuration

> [!CAUTION]
> Never expose ports `5432` (PostgreSQL) or `6379` (Redis) to `0.0.0.0/0`. They must remain accessible exclusively within the internal Docker network.

| Protocol | Port | Source | Description |
| :--- | :--- | :--- | :--- |
| **SSH (TCP)** | `22` | `YOUR_OFFICE_IP/32` | Administrative SSH access (strictly restricted) |
| **HTTP (TCP)** | `80` | `0.0.0.0/0`, `::/0` | Let's Encrypt HTTP-01 challenge & HTTP -> HTTPS redirect |
| **HTTPS (TCP)** | `443` | `0.0.0.0/0`, `::/0` | Encrypted public REST API traffic |

### AWS CLI Security Group Setup
```bash
# 1. Create dedicated Security Group
SG_ID=$(aws ec2 create-security-group \
    --group-name "agentkit-sg" \
    --description "Security Group for AgentKit EC2 Host" \
    --query "GroupId" --output text)

# 2. Allow SSH only from your trusted IP
aws ec2 authorize-security-group-ingress \
    --group-id "$SG_ID" \
    --protocol tcp --port 22 --cidr "203.0.113.45/32"

# 3. Allow public HTTP/HTTPS for Web & ACME TLS
aws ec2 authorize-security-group-ingress \
    --group-id "$SG_ID" \
    --protocol tcp --port 80 --cidr "0.0.0.0/0"
aws ec2 authorize-security-group-ingress \
    --group-id "$SG_ID" \
    --protocol tcp --port 443 --cidr "0.0.0.0/0"
```

---

## 4. Host Setup & Docker Installation

Connect to your EC2 instance via SSH:
```bash
ssh -i /path/to/key.pem ubuntu@<EC2_PUBLIC_IP>
```

Update system packages and install official Docker CE and Docker Compose:
```bash
# Update repository lists
sudo apt-get update && sudo apt-get upgrade -y
sudo apt-get install -y ca-certificates curl gnupg lsb-release

# Install Docker GPG key
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

# Add Docker repository
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

# Install Docker Engine and Compose plugin
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# Enable Docker daemon on boot and add ubuntu user to docker group
sudo systemctl enable --now docker
sudo usermod -aG docker ubuntu
newgrp docker
```

---

## 5. Storage & Volume Persistence

Create the application directory structure:
```bash
sudo mkdir -p /opt/agentkit /var/log/agentkit
sudo chown -R ubuntu:ubuntu /opt/agentkit /var/log/agentkit
cd /opt/agentkit
```

Clone the repository:
```bash
git clone https://github.com/getdownandcode/AgentKit.git .
```

Verify the local volume directories:
- `postgres_data`: Docker managed volume or mount at `/opt/agentkit/volumes/postgres`
- `redis_data`: Docker managed volume or mount at `/opt/agentkit/volumes/redis`

---

## 6. Environment & Secrets Management

Store secrets securely using AWS Systems Manager (SSM) Parameter Store or AWS Secrets Manager.

### Option A: AWS SSM Parameter Store Integration
Fetch production secrets dynamically using the AWS CLI:

```bash
# Create parameters in AWS SSM
aws ssm put-parameter --name "/agentkit/production/DATABASE_PASSWORD" \
    --value "$(openssl rand -hex 24)" --type "SecureString"
aws ssm put-parameter --name "/agentkit/production/API_KEYS" \
    --value "prod_key_$(openssl rand -hex 16)" --type "SecureString"
aws ssm put-parameter --name "/agentkit/production/GEMINI_API_KEY" \
    --value "AIzaSy..." --type "SecureString"

# Export parameters directly into restricted .env file on the host
cat << 'EOF' > /opt/agentkit/fetch_secrets.sh
#!/usr/bin/env bash
set -euo pipefail

DB_PASS=$(aws ssm get-parameter --name "/agentkit/production/DATABASE_PASSWORD" --with-decryption --query "Parameter.Value" --output text)
API_KEYS=$(aws ssm get-parameter --name "/agentkit/production/API_KEYS" --with-decryption --query "Parameter.Value" --output text)
GEMINI_KEY=$(aws ssm get-parameter --name "/agentkit/production/GEMINI_API_KEY" --with-decryption --query "Parameter.Value" --output text)

cat << ENVFILE > /opt/agentkit/.env
DATABASE_URL=postgresql+asyncpg://agentkit:${DB_PASS}@postgres:5432/agentkit
POSTGRES_USER=agentkit
POSTGRES_PASSWORD=${DB_PASS}
POSTGRES_DB=agentkit
REDIS_URL=redis://redis:6379/0
LLM_PROVIDER=gemini
LLM_MODEL=gemini-3.1-flash-lite
GEMINI_API_KEY=${GEMINI_KEY}
API_KEYS=${API_KEYS}
FILE_TOOL_BASE_DIR=/tmp/agentkit_sandbox
RUN_TIMEOUT_S=60
MAX_RETRIES=3
ENVFILE

chmod 600 /opt/agentkit/.env
echo ".env successfully updated from AWS SSM."
EOF

chmod +x /opt/agentkit/fetch_secrets.sh
./opt/agentkit/fetch_secrets.sh
```

### Option B: Manual Host-Level `.env` Configuration
If configuring manually, create `/opt/agentkit/.env` from `.env.example`:
```bash
cp .env.example .env
chmod 600 .env  # Restrict permissions: read/write only for owner
```

---

## 7. Systemd Service Daemon

Create a systemd service file at `/etc/systemd/system/agentkit.service` to manage the lifecycle of the Docker Compose stack across host restarts.

```ini
[Unit]
Description=AgentKit Docker Compose Application Service
Requires=docker.service
After=docker.service network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/opt/agentkit
User=ubuntu
Group=docker

# Pre-execution: pull latest images or fetch credentials
ExecStartPre=-/usr/bin/docker compose pull --quiet
# Execution: start stack in detached mode
ExecStart=/usr/bin/docker compose up -d --remove-orphans
# Teardown: stop stack gracefully
ExecStop=/usr/bin/docker compose down --timeout 30

# Restart policy for the service unit itself
Restart=on-failure
RestartSec=10s
TimeoutStartSec=300

[Install]
WantedBy=multi-user.target
```

Reload systemd and enable the service on boot:
```bash
sudo systemctl daemon-reload
sudo systemctl enable agentkit.service
sudo systemctl start agentkit.service
sudo systemctl status agentkit.service
```

---

## 8. Reverse Proxy & Automatic SSL Termination

### Option A: Caddy (Recommended - Zero Config Automated SSL)

Caddy automatically obtains and renews Let's Encrypt TLS certificates with no cron jobs required.

1. Install Caddy on Ubuntu:
```bash
sudo apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLF 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLF 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt-get update
sudo apt-get install -y caddy
```

2. Configure `/etc/caddy/Caddyfile`:
```caddy
api.yourdomain.com {
    # Automatic TLS enabled by default
    encode gzip zstd

    # Security Headers
    header {
        Strict-Transport-Security "max-age=31536000; includeSubDomains; preload"
        X-Content-Type-Options "nosniff"
        X-Frame-Options "DENY"
        Referrer-Policy "strict-origin-when-cross-origin"
    }

    # Proxy to AgentKit FastAPI backend
    reverse_proxy localhost:8000 {
        header_up Host {host}
        header_up X-Real-IP {remote_host}
        header_up X-Forwarded-For {remote_host}
        header_up X-Forwarded-Proto {scheme}

        # Healthcheck passive circuit breaker
        fail_duration 10s
        max_fails 3
    }

    # Access logging
    log {
        output file /var/log/caddy/agentkit_access.log {
            roll_size 50mb
            roll_keep 5
        }
    }
}
```

3. Restart Caddy:
```bash
sudo systemctl restart caddy
```

---

### Option B: Nginx + Certbot

If your organization standardizes on Nginx:

1. Install Nginx and Certbot:
```bash
sudo apt-get install -y nginx certbot python3-certbot-nginx
```

2. Create `/etc/nginx/sites-available/agentkit`:
```nginx
server {
    server_name api.yourdomain.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # WebSocket support (for streaming endpoints)
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";

        # Timeouts for long agent reasoning runs
        proxy_connect_timeout 60s;
        proxy_send_timeout 120s;
        proxy_read_timeout 120s;
    }

    listen 80;
}
```

3. Enable site and generate SSL certificate:
```bash
sudo ln -s /etc/nginx/sites-available/agentkit /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl restart nginx

# Obtain certificate via Let's Encrypt Certbot
sudo certbot --nginx -d api.yourdomain.com --non-interactive --agree-tos -m admin@yourdomain.com
```

---

## 9. Deployment & Verification Checklist

Once the stack is running, execute the verification checklist:

1. **Verify Docker Containers**:
   ```bash
   docker compose ps
   # All 3 services (postgres, redis, api) should show 'healthy' status
   ```

2. **Verify Database Migrations**:
   ```bash
   docker compose logs api | grep -i "running migrations"
   ```

3. **Verify API Healthcheck (Loopback)**:
   ```bash
   curl -s http://127.0.0.1:8000/health | jq .
   # Expected output:
   # {
   #   "status": "healthy",
   #   "database": "connected",
   #   "redis": "connected",
   #   "version": "0.1.0"
   # }
   ```

4. **Verify HTTPS & SSL Termination**:
   ```bash
   curl -I https://api.yourdomain.com/health
   # Expected: HTTP/2 200 OK with valid TLS certificate
   ```

5. **Verify Authenticated Agent Run**:
   ```bash
   curl -X POST https://api.yourdomain.com/runs \
     -H "X-API-Key: YOUR_CONFIGURED_API_KEY" \
     -H "Content-Type: application/json" \
     -d '{"goal": "Calculate 42 * 100 using calculator", "session_id": "test-session"}' | jq .
   ```

---

## 10. Monitoring, Backups & Maintenance

### Automated PostgreSQL Backup Cron
Configure daily automated backups to AWS S3:
```bash
# Add backup script /opt/agentkit/backup_db.sh
cat << 'EOF' > /opt/agentkit/backup_db.sh
#!/usr/bin/env bash
set -euo pipefail
TIMESTAMP=$(date +\%Y\%m\%d_\%H\%M\%S)
BACKUP_FILE="/tmp/agentkit_backup_${TIMESTAMP}.sql.gz"

docker compose exec -T postgres pg_dump -U agentkit agentkit | gzip > "$BACKUP_FILE"
aws s3 cp "$BACKUP_FILE" "s3://your-agentkit-backups-bucket/db/${TIMESTAMP}.sql.gz"
rm -f "$BACKUP_FILE"
echo "Backup ${TIMESTAMP} completed successfully."
EOF

chmod +x /opt/agentkit/backup_db.sh

# Add to crontab: daily at 02:00 UTC
(crontab -l 2>/dev/null; echo "0 2 * * * /opt/agentkit/backup_db.sh >> /var/log/agentkit/backup.log 2>&1") | crontab -
```

### Docker Log Management
To prevent containers from exhausting disk space, ensure Docker daemon log rotation is configured in `/etc/docker/daemon.json`:
```json
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "50m",
    "max-file": "3"
  }
}
```
Apply with `sudo systemctl restart docker`.
