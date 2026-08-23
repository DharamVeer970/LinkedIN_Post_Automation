# LinkedIn Auto-Post via GitHub Actions (Cloud Scheduling)

Runs every 2 days on GitHub's servers, so it works **even when your laptop is
shut down**. No server rental, no VPN — just free GitHub Actions minutes.

> **Caveat:** GitHub Actions **free / free-for-public** repos get up to
> 2,000 minutes/month. A single scheduled run takes ~1–3 min, so running
> twice a month uses a few minutes — well within free limits.

---

## 1. Create a GitHub repository

- Go to https://github.com/new → create an **empty** repo (do NOT add a
  README/.gitignore there — we have our own).

## 2. Push this project to GitHub

Open a terminal in this folder and run:

```bash
git init
git add .
git commit -m "Initial commit: LinkedIn auto-post pipeline"
git branch -M main
git remote add origin https://github.com/<YOUR_USERNAME>/<REPO_NAME>.git
git push -u origin main
```

> The `.gitignore` prevents `.env`, `images/`, `logs/` and `__pycache__`
> from being committed, so your API keys stay out of the repo.

## 3. Add your API keys as GitHub Secrets

Go to: **Repo → Settings → Secrets and variables → Actions → New repository secret**

Add all three:

| Secret name        | Value                                    |
|--------------------|------------------------------------------|
| `GEMINI_API_KEY`   | your Gemini API key (from `.env`)        |
| `LINKEDIN_TOKEN`   | your LinkedIn OAuth token (from `.env`)  |
| `STABILITY_AI`     | your Stability AI key (from `.env`)      |

These are encrypted and are only injected into the GitHub-hosted runner at
runtime — they are never committed to the repo or visible in logs.

## 4. Let it run

- The workflow `.github/workflows/auto_post.yml` triggers on the cron
  `"0 9 */2 * *"` → **every 2 days at 09:00 UTC** (2:30 PM IST / 4 AM ET / 1 AM PT).
- Watch status: **Repo → Actions → *LinkedIn Auto-Post***.
- Run it manually anytime with the **"Run workflow"** button.

You get an email from GitHub if a run fails.

---

## What happens on each run

```
[cloud runner starts]
  → create .env from Secrets (GEMINI_API_KEY, LINKEDIN_TOKEN, STABILITY_AI)
  → pip install -r requirements.txt
  → python linkedin_pipeline.py
       pick_topic → generate_content → critique/revise →
       image_prompt → uniqueness check → Stability AI image → post to LinkedIn
  → commit posts_history.json back (git-friendly; re-seeds ChromaDB next run)
[published to LinkedIn automatically]
```

---

## Keeping Secrets in sync with `.env`

Every time you change a key in your local `.env`, update the matching
**GitHub Secret** too, otherwise the scheduled runs use stale credentials.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Run fails on "model not found" | Update `GEMINI_MODEL` in `linkedin_pipeline.py` |
| `429 Too Many Requests` | Free quota hit — the pipeline retries automatically; check https://ai.dev/rate-limit |
| "Not authorized / Invalid token" | Refresh the LinkedIn OAuth token via `linkedin_poster.py`, then update the `LINKEDIN_TOKEN` secret |
| No post appears on LinkedIn | Check the repo **Actions** tab logs; re-run manually to retry |
| Two posts on the same day | The cron is 2-day cadence; check you only have one `schedule` entry |