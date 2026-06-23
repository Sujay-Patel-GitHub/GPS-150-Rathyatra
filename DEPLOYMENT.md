# Deployment Guide

How code gets from your machine to the live server automatically.

---

## How It Works (Overview)

```
Your PC  →  git push  →  GitHub  →  GitHub Actions  →  SSH into server  →  git pull + restart
```

Every time you push to the `main` branch, GitHub automatically runs a workflow that SSHs into the server, pulls the latest code, and restarts the app. You never touch the server manually.

---

## File That Controls This

`.github/workflows/deploy.yml`

This is the GitHub Actions workflow file. It lives inside your repo and GitHub reads it automatically.

---

## Step-by-Step: What Happens on `git push`

1. **You push** any commit to `main` branch on GitHub.

2. **GitHub Actions triggers** — a fresh Ubuntu machine (runner) starts up on GitHub's servers.

3. **Runner SSHs into your server** at `103.250.160.75` using credentials stored in GitHub Secrets (not hardcoded in the file).

4. **On the server, it runs:**
   ```bash
   # Go to the project folder
   cd "/home/lenovo/6-2-2026-GPS/Latest GPS"

   # Pull the latest code from GitHub
   git fetch https://x-access-token:<GITHUB_TOKEN>@github.com/... main
   git reset --hard FETCH_HEAD

   # Install any new Python packages
   ./venv/bin/python3 -m pip install -r requirements.txt

   # Restart the Flask app via systemd
   sudo systemctl restart gps-server.service
   ```

5. **Verifies** the app came back up by checking `pgrep -f "app.py"` after 8 seconds. If it didn't start, the workflow fails and shows the last 40 lines of `output.log`.

---

## GitHub Secrets (Where Credentials Are Stored)

Go to: `GitHub repo → Settings → Secrets and variables → Actions`

| Secret Name | Value |
|---|---|
| `SERVER_HOST` | `103.250.160.75` |
| `SERVER_USER` | `lenovo` |
| `SSH_PRIVATE_KEY` | Private SSH key of the server (or password via `password` field) |

These are never visible in the workflow file — GitHub injects them securely at runtime.

---

## The App on the Server

The Flask app (`app.py`) runs as a **systemd service** called `gps-server.service`.

- **Port:** `7777`
- **Location:** `/home/lenovo/6-2-2026-GPS/Latest GPS/app.py`
- **Venv:** `./venv/`

Useful commands if you ever SSH in manually:

```bash
# Check status
sudo systemctl status gps-server.service

# View live logs
sudo journalctl -u gps-server.service -f

# Restart manually
sudo systemctl restart gps-server.service

# Stop
sudo systemctl stop gps-server.service
```

---

## Your Local Workflow (Day to Day)

```bash
# 1. Make your changes in the code

# 2. Stage the changed files
git add <file1> <file2>

# 3. Commit
git commit -m "What you changed"

# 4. Push — this triggers the auto-deploy
git push origin main
```

That's it. The server updates itself within ~15 seconds of the push.

---

## Checking Deployment Status

After you push, go to:  
`GitHub repo → Actions tab`

You'll see the latest workflow run. Green = deployed successfully. Red = something failed (click it to see the error log).

---

## What NOT to Do

- **Don't run `python app.py` manually on the server** — systemd is already running it. Two instances fighting over port 7777 will crash both.
- **Don't edit files directly on the server** — next deploy will overwrite them (`git reset --hard`).
- **Don't skip the venv** — always use `./venv/bin/python3`, not the system Python.

---

## If Deployment Fails

1. Go to GitHub → Actions → click the failed run → read the error.
2. Common causes:
   - **SSH auth failure** — check GitHub Secrets are correct.
   - **pip install error** — a new package in `requirements.txt` failed to install.
   - **app.py didn't start** — syntax error in code; check `output.log` on the server.
3. Fix the issue, push again — the workflow re-runs automatically.
