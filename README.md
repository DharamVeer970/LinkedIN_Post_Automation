# LinkedIn Post AI — Automated Viral Post Pipeline

> Autonomous LangGraph pipeline that generates scroll-stopping LinkedIn posts (text + image) and publishes them on schedule. Text by **Gemini (3.6 Flash + 3.1 Flash Lite fallback)**, images by **Cloudflare Workers AI (FLUX.2 klein-4b)**, orchestration by **LangGraph**, duplicate guard by **ChromaDB**.

![Python 3.12](https://img.shields.io/badge/python-3.12-blue)
![LangGraph](https://img.shields.io/badge/LangGraph-0.2-orange)
![Cloudflare](https://img.shields.io/badge/image-Cloudflare%20Workers%20AI-orange)

---

## ✨ What it does

1. Picks a **fresh topic** from live RSS feeds + curated evergreen lists (AI, Automation, DevNews, Business, Career, Psychology, Science, Design, Money).
2. Writes the post with **Gemini 3.6 Flash** (auto-fallback to 3.1 Flash Lite on quota exhaustion), self-critiques & revises until `score ≥ 7/10`.
3. Generates an **agent-driven image prompt** (Gemini picks 1 of 7 viral templates) and renders it via **Cloudflare Workers AI** (`flux-2-klein-4b` → `flux-2-dev` → `flux-1-schnell` → SDXL fallback chain).
4. Checks uniqueness via **ChromaDB**, publishes to LinkedIn (OAuth v2), and persists history for the next run.

---

## 📁 Project Structure

```
LinkedIn_Post_Automation/
├── linkedin_pipeline.py      # Main LangGraph pipeline (8 nodes)
├── post_prompts.py           # Single source of truth: domains, templates, prompts
├── linkedin_poster.py        # LinkedIn OAuth + image upload + post creation
├── requirements.txt          # Python deps
├── .env.example              # Template for required env vars
│
├── .gitignore                # Ignores .env, images/, post_history_db/
├── post_history_db/          # ChromaDB persistent store (local only, git-ignored)
├── images/                   # Generated images (git-ignored)
├── posts_history.json        # Git-tracked post history for persistence / seeding
├── .github/workflows/auto_post.yml  # Cloud scheduler — every 4 days at 09:00 UTC
│
└── GITHUB_ACTIONS_SETUP.md   # Step-by-step cloud deploy guide
```

---

## 🔄 Pipeline Flow (LangGraph)

```
pick_topic ──► generate_content ──► critique_post ───┐
                              ▲                      │
                         revise_content ◄────────────┘
                              │ (score ≥ 7 or 2 revisions)
                              ▼
                  generate_image_prompt (Gemini picks 1 of 7 templates)
                              │
                              ▼
                    check_uniqueness (ChromaDB, threshold 0.85)
                         │         │
                    unique    duplicate ──► pick_topic (retry ×3)
                         │
                         ▼
                    generate_image (Cloudflare Workers AI, flux-2-klein-4b)
                         │
                         ▼
                  post_to_linkedin (upload + create post)
                         │
                         ▼
                    save_history (posts_history.json + ChromaDB)
```

**Nodes in `linkedin_pipeline.py`:**
| Node | File:Line | Description |
|---|---|---|
| `pick_topic` | `linkedin_pipeline.py:266` | Random from `get_topic_pool()` (RSS + evergreen), avoids `tried_topics` |
| `generate_content` | `linkedin_pipeline.py:280` | `call_gemini(content_prompt())` |
| `critique_post` | `linkedin_pipeline.py:293` | LLM critic + deterministic penalties (clichés, hashtags, emojis) |
| `revise_content` | `linkedin_pipeline.py:347` | `call_gemini(revise_prompt())` |
| `generate_image_prompt` | `linkedin_pipeline.py:356` | `call_gemini(image_prompt_gen())` — 7 templates |
| `check_uniqueness` | `linkedin_pipeline.py:363` | `collection.query()` cosine distance |
| `generate_image` | `linkedin_pipeline.py:500` | Cloudflare Workers AI `POST /accounts/{id}/ai/run/@cf/black-forest-labs/flux-2-klein-4b` |
| `post_to_linkedin` | `linkedin_pipeline.py:527` | `linkedin_poster.py:52,62,78,86` |
| `save_history` | `linkedin_pipeline.py:537` | Appends to history + ChromaDB |

---

## 🖼️ Image Templates (Agent-Driven)

Gemini chooses **exactly one** per post. Defined in `post_prompts.py:190` `VIRAL_TEMPLATES`:

| Key | Use When | Style |
|---|---|---|
| `comic` | Humor / AI fails / relatable work | 6-panel 3×2 comic, flat vector, speech bubbles ≤8 words |
| `roadmap` | Tech stacks / skills | Dark navy glowing flowchart, 7 glass cards, cyan circuit traces |
| `comparison` | X vs Y (GPU vs TPU) | 4 cards 2×2, color-coded headers, isometric hero, cream grid |
| `sketch_story` | Paradox / narrative | Hand-drawn ink, bridge metaphor, orange/blue highlights |
| `billboard` | Listicles (How to stay poor) | Huge headline + 6 icons 2×3 grid, high contrast |
| `animated-image` | Multi-panel story progression | 6-panel story flow, clean vector cartoon, speech bubbles ≤8 words |
| `automation-flow-diagram` | Process / workflow automation | Clear flowchart with connecting lines, simple background, step-by-step |

Prompt rule `post_prompts.py:323`: **every word in the image must come from the caption** — no generic labels. Ends with `crisp vector, 8k, ultra-detailed, perfectly legible English`.

---

## 🚀 Quick Start (Local)

```bash
# 1. Clone & install
git clone https://github.com/DharamVeer970/LinkedIN_Post_Automation.git && cd LinkedIN_Post_Automation
pip install -r requirements.txt

# 2. Configure secrets
cp .env.example .env
# Edit .env — set these values:
# GEMINI_API_KEY=...
# LINKEDIN_TOKEN=...          # from linkedin_poster.py auth flow
# CLOUDFLARE_ACCOUNT_ID=...
# CLOUDFLARE_API_KEY=...
# CLIENT_ID=... / CLIENT_SECRET=...  # LinkedIn Developer App

# 3. One-time LinkedIn auth (gets LINKEDIN_TOKEN)
python linkedin_poster.py
# → open the printed URL, authorize, paste the ?code= value

# 4. Run once
python linkedin_pipeline.py
# Check images/ and LinkedIn feed
```

### Requirements `requirements.txt:1`

```
requests>=2.28
python-dotenv>=1.0
feedparser>=6.0
chromadb>=0.4
langgraph>=0.2
pillow>=10.0
```

---

## 🔑 Environment Variables

| Var | Source | Required | Notes |
|---|---|---|---|
| `GEMINI_API_KEY` | https://aistudio.google.com/apikey | ✅ | Gemini 3.6 Flash + 3.1 Flash Lite fallback (`linkedin_pipeline.py:67`) |
| `CLOUDFLARE_ACCOUNT_ID` | https://dash.cloudflare.com (Workers & Pages → sidebar) | ✅ | Cloudflare account ID |
| `CLOUDFLARE_API_KEY` | https://dash.cloudflare.com/profile/api-tokens | ✅ | API token with **Workers AI: Edit** permission (`linkedin_pipeline.py:59`) |
| `LINKEDIN_TOKEN` | `linkedin_poster.py:26` OAuth | ✅ | ~60 days validity |
| `CLIENT_ID` / `CLIENT_SECRET` | LinkedIn Developer Portal | ✅ (once) | For `get_authorization_url()` |

`.env` is git-ignored (`.gitignore:3`). Never commit it.

---

## ☁️ Cloud Scheduling (GitHub Actions)

Runs headless every 4 days — no laptop needed. See `GITHUB_ACTIONS_SETUP.md`.

- Workflow: `.github/workflows/auto_post.yml:12` — Daily cron (`0 9 * * *`) with `day_of_year % 4 == 0` check (09:00 UTC), plus `workflow_dispatch` for manual trigger
- Runner: `ubuntu-latest` + Python `3.12`
- Secrets: `GEMINI_API_KEY`, `LINKEDIN_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_API_KEY` (Repo → Settings → Secrets and variables → Actions)
- Persistence: workflow commits history back to survive ephemeral runners; `_seed_from_history()` (`linkedin_pipeline.py:91`) rehydrates ChromaDB each run

```bash
git init && git add . && git commit -m "Initial commit"
git branch -M main && git remote add origin https://github.com/DharamVeer970/LinkedIN_Post_Automation.git
git push -u origin main
# Add 4 secrets in GitHub → watch Actions tab → Run workflow
```

---

## 🎛️ Customization

All prompts & domains live in **`post_prompts.py`** — edit that file only:

- **Topics:** `TOPIC_DOMAINS` (`post_prompts.py:13`) — add RSS `feeds` or `evergreen` strings. Auto-aggregated via `ALL_RSS_FEEDS` / `ALL_EVERGREEN_TOPICS`.
- **Caption style:** `content_prompt()` (`post_prompts.py:252`) — hook, 3-4 punchy paras, 5-7 emojis, woven hashtags, final hashtag line.
- **Critic rules:** `critique_prompt()` / `CLICHES` (`linkedin_pipeline.py:287`) — quality gate `QUALITY_GATE=7` (`linkedin_pipeline.py:71`).
- **Image styles:** `VIRAL_TEMPLATES` (`post_prompts.py:190`) + `image_prompt_gen()` (`post_prompts.py:308`).

---

## 🔧 Troubleshooting

| Symptom | Fix |
|---|---|
| `GEMINI_API_KEY or LINKEDIN_TOKEN missing` | Check `.env` exists and is loaded (`load_dotenv()`); on Actions check Secrets names match exactly |
| `Cloudflare ... error 401/403` | Wrong/expired API token — create one at dash.cloudflare.com/profile/api-tokens with **Workers AI: Edit** permission |
| `Cloudflare ... error 7003/7000` (no route) | Wrong `CLOUDFLARE_ACCOUNT_ID` — copy it from Workers & Pages sidebar |
| `429 Gemini` / `model not found` | Pipeline auto-retries with jitter, then falls back across `GEMINI_MODELS` (`linkedin_pipeline.py:67`). If persistent, wait for quota reset |
| `Cloudflare 429: daily free allocation of neurons` | Workers AI free tier = 10,000 neurons/day (resets midnight PT). Each image costs neurons; QA retries cost more. Wait for reset or upgrade to Workers Paid |
| `Not authorized / Invalid token` | LinkedIn token expired (~60d). Re-run `python linkedin_poster.py` and update `.env` + GitHub Secret `LINKEDIN_TOKEN` |
| ChromaDB empty on Actions | Ensure `posts_history.json` is committed — `_seed_from_history()` needs it |
| Image text gibberish | Keep bubble/label fragments ≤8 words (enforced in templates) — Cloudflare FLUX renders short text best |

---

> Built with Gemini • Cloudflare Workers AI • LangGraph • ChromaDB • LinkedIn API v202608 (`linkedin_poster.py:14`)
