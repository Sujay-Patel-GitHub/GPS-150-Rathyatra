# New Server Setup Guide

How to take a fresh server and make it so that every `git push` to GitHub automatically updates the server code and restarts the app.

---

## Overview

```
GitHub Repo  →  git push  →  GitHub Actions  →  SSH into server  →  git pull + restart
```

This guide sets up the server side. Once done, you never SSH in again for deployments — just push to GitHub.

---

## Step 1 — SSH Into the New Server

```bash
ssh lenovo@<SERVER_IP>
```

---

## Step 2 — Install System Dependencies

```bash
sudo apt update && sudo apt upgrade -y

# Python and pip
sudo apt install -y python3 python3-pip python3-venv git curl

# MongoDB 4.4 (use this version — works without AVX CPU support)
# First install libssl1.1 (required by MongoDB 4.4)
curl -sO http://archive.ubuntu.com/ubuntu/pool/main/o/openssl/libssl1.1_1.1.1f-1ubuntu2_amd64.deb
sudo dpkg -i libssl1.1_1.1.1f-1ubuntu2_amd64.deb

# Add MongoDB 4.4 repo
curl -fsSL https://www.mongodb.org/static/pgp/server-4.4.asc | sudo apt-key add -
echo "deb [ arch=amd64,arm64 ] https://repo.mongodb.org/apt/ubuntu focal/mongodb-org/4.4 multiverse" \
  | sudo tee /etc/apt/sources.list.d/mongodb-org-4.4.list
sudo apt update
sudo apt install -y mongodb-org=4.4.29 mongodb-org-server=4.4.29 \
  mongodb-org-shell=4.4.29 mongodb-org-mongos=4.4.29 mongodb-org-tools=4.4.29

# Start and enable MongoDB
sudo systemctl start mongod
sudo systemctl enable mongod
sudo systemctl status mongod   # should say: active (running)
```

> **Why MongoDB 4.4?** Versions 5.0 and above require AVX CPU instructions. Many VPS servers do not have AVX. Version 4.4 works on all hardware.

---

## Step 3 — Clone the GitHub Repo

```bash
cd /home/lenovo
git clone https://github.com/Sujay-Patel-GitHub/Rath-Yatra-103-server.git "Latest GPS"
cd "Latest GPS"
```

---

## Step 4 — Create Python Virtual Environment

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
```

---

## Step 5 — Create the systemd Service

This makes the app start automatically on boot and restart if it crashes.

```bash
sudo nano /etc/systemd/system/gps-server.service
```

Paste this content (replace `<SERVER_IP>` and path if different):

```ini
[Unit]
Description=GPS Flask Server
After=network.target mongod.service

[Service]
User=lenovo
WorkingDirectory=/home/lenovo/Latest GPS
ExecStart=/home/lenovo/Latest GPS/venv/bin/python3 app.py
Restart=always
RestartSec=5
StandardOutput=append:/home/lenovo/Latest GPS/output.log
StandardError=append:/home/lenovo/Latest GPS/output.log

[Install]
WantedBy=multi-user.target
```

Save and enable:

```bash
sudo systemctl daemon-reload
sudo systemctl enable gps-server.service
sudo systemctl start gps-server.service
sudo systemctl status gps-server.service   # should say: active (running)
```

Check logs if it fails:
```bash
tail -50 "/home/lenovo/Latest GPS/output.log"
```

---

## Step 6 — Allow sudo Restart Without Password (Required for GitHub Actions)

GitHub Actions SSHs in as `lenovo` and needs to restart the service. Give it passwordless sudo for just that one command:

```bash
sudo visudo
```

Add this line at the bottom:
```
lenovo ALL=(ALL) NOPASSWD: /bin/systemctl restart gps-server.service
```

---

## Step 7 — Generate SSH Key for GitHub Actions

On the **server**, generate a key pair:

```bash
ssh-keygen -t ed25519 -C "github-actions-deploy" -f ~/.ssh/deploy_key -N ""
```

Add the public key to authorized_keys so GitHub can SSH in:

```bash
cat ~/.ssh/deploy_key.pub >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
```

Copy the **private key** — you'll paste it into GitHub Secrets:

```bash
cat ~/.ssh/deploy_key
```

---

## Step 8 — Add GitHub Secrets

Go to your GitHub repo → **Settings → Secrets and variables → Actions → New repository secret**

Add these 3 secrets:

| Secret Name | Value |
|---|---|
| `SERVER_HOST` | Your server IP (e.g. `150.129.165.162`) |
| `SERVER_USER` | `lenovo` (or whatever your username is) |
| `SSH_PRIVATE_KEY` | The entire private key from Step 7 (starts with `-----BEGIN OPENSSH PRIVATE KEY-----`) |

---

## Step 9 — Add the Deploy Workflow to the Repo

The file `.github/workflows/deploy.yml` already exists in this repo. It handles everything automatically. The key parts:

```yaml
on:
  push:
    branches:
      - main        # triggers on every push to main

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: appleboy/ssh-action@master
        with:
          host: ${{ secrets.SERVER_HOST }}
          username: ${{ secrets.SERVER_USER }}
          key: ${{ secrets.SSH_PRIVATE_KEY }}
          script: |
            cd "/home/lenovo/Latest GPS"
            git fetch https://x-access-token:${{ secrets.GITHUB_TOKEN }}@github.com/${{ github.repository }}.git main
            git reset --hard FETCH_HEAD
            ./venv/bin/python3 -m pip install -r requirements.txt
            sudo systemctl restart gps-server.service
```

**Update the `cd` path** in the script if your folder name is different.

---

## Step 10 — Test It

Make any small change to any file, commit and push:

```bash
git add .
git commit -m "Test deploy"
git push origin main
```

Then go to **GitHub → Actions tab** — you should see the workflow run and succeed (green checkmark).

---

## Day-to-Day Workflow After Setup

```bash
# 1. Edit code locally on your PC
# 2. Stage changes
git add <file>

# 3. Commit
git commit -m "What you changed"

# 4. Push — server updates automatically
git push origin main
```

That's it. Server updates itself within ~15 seconds.

---

## Useful Server Commands

```bash
# Check if app is running
sudo systemctl status gps-server.service

# View live app logs
tail -f "/home/lenovo/Latest GPS/output.log"

# Restart manually
sudo systemctl restart gps-server.service

# Check MongoDB
sudo systemctl status mongod
mongo --eval "db.adminCommand({listDatabases:1})"

# Check disk space
df -h /
```

---

## MongoDB Notes

- MongoDB is on `localhost:27017` only (not exposed to internet — safe)
- Auth is **disabled** (fine since it's localhost-only)
- If MongoDB crashes on startup with `signal=ILL` → CPU has no AVX → use MongoDB 4.4 (see Step 2)
- If disk fills up → MongoDB log and syslog are the usual culprits:
  ```bash
  sudo truncate -s 0 /var/log/mongodb/mongod.log
  sudo truncate -s 0 /var/log/syslog
  sudo journalctl --vacuum-size=200M
  ```

---

## Servers Reference

| Server | IP | Repo | App Port | Username |
|---|---|---|---|---|
| Main GPS Server | `103.250.160.75` | `GPS-Project` | `7777` | `lenovo` |
| Rath Yatra Server | `150.129.165.162` | `Rath-Yatra-103-server` | `7777` | `lenovo1` |
