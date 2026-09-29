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
- **Workload Profile**: Development / Staging: `t3.small` (2 vCPU, 2 GiB RAM)
- **Workload Profile**: Production Minimum: `t3.medium` (2 vCPU, 4 GiB RAM)
- **Workload Profile**: High Throughput: `c6i.large` or `c7g.large` (2 vCPU, 4 GiB RAM, Compute-optimized)
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
