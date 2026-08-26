# LinkedIn Post AI — Automated Viral Post Pipeline

> Autonomous LangGraph pipeline that generates scroll-stopping LinkedIn posts (text + image) and publishes them on schedule. Text by **Gemini**, images by **Stability AI Ultra**, orchestration by **LangGraph**, duplicate guard by **ChromaDB**.

![Python 3.12](https://img.shields.io/badge/python-3.12-blue)
![LangGraph](https://img.shields.io/badge/LangGraph-0.2-orange)
![Stability AI](https://img.shields.io/badge/image-Stability%20Ultra-purple)

---

## ✨ What it does

1. Picks a **fresh topic** from live RSS feeds + curated evergreen lists (AI, Automation, DevNews, Business, Career, Psychology, Science, Design, Money).
2. Writes the post with **Gemini 3.6 Flash**, self-critiques & revises until `score ≥ 7/10`.
3. Generates an **agent-driven image prompt** (Gemini picks 1 of 5 viral templates) and renders it via **Stability AI Ultra** (`api.stability.ai/v2beta/stable-image/generate/ultra`).
4. Checks uniqueness via **ChromaDB**, publishes to LinkedIn (OAuth v2), and persists history for the next run.

---

## 📁 Project Structure

```
LinkedIn_Post_Automation/
├── linkedin_pipeline.py      # Main LangGraph pipeline (8 nodes)
├── post_prompts.py           # Single source of truth: domains, templates,
├── linkedin_poster.py        # LinkedIn OAuth + image upload + post creation
├── requirements.txt          # Python deps
├── .env.example              # Template for required env vars
|
├── .gitignore                # Ignores .env, images/, post_history_db/
├── post_history_db/          # ChromaDB persistent store (local only, git-ignored)
├── images/                   # Generated .webp images (git-ignored)
├── .github/workflows/auto_post.yml  # Cloud scheduler — every 2 days at 
|
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
                  generate_image_prompt (Gemini picks template A-E)
                              │
                              ▼
                    check_uniqueness (ChromaDB, threshold 0.85)
                         │         │
                    unique    duplicate ──► pick_topic (retry ×3)
                         │
                         ▼
                    generate_image (Stability Ultra, output_format=webp)
                         │
                         ▼
                  post_to_linkedin (upload + create post)
                         │
                         ▼
                    save_history (posts_history.json + ChromaDB)
```

**Nodes in `linkedin_pipeline.py`:**
| Node | File:Line | Description |
|------|-----------|-------------|
| `pick_topic` | `linkedin_pipeline.py:208` | Random from `get_topic_pool()` (RSS + evergreen), avoids `tried_topics` |
| `generate_content` | `linkedin_pipeline.py:222` | `call_gemini(content_prompt())` |
| `critique_post` | `linkedin_pipeline.py:235` | LLM critic + deterministic penalties (clichés, hashtags, emojis) |
| `revise_content` | `linkedin_pipeline.py:289` | `call_gemini(revise_prompt())` |
| `generate_image_prompt` | `linkedin_pipeline.py:298` | `call_gemini(image_prompt_gen())` — 5 templates |
| `check_uniqueness` | `linkedin_pipeline.py:305` | `collection.query()` cosine distance |
| `generate_image` | `linkedin_pipeline.py:325` | Stability Ultra `POST /generate/ultra` as in your snippet |
| `post_to_linkedin` | `linkedin_pipeline.py:362` | `linkedin_poster.py:52,62,78,86` |
| `save_history` | `linkedin_pipeline.py:372` | Appends to history + ChromaDB |

---

## 🖼️ Image Templates (Agent-Driven)

Gemini chooses **exactly one** per post. Defined in `post_prompts.py:168` `VIRAL_TEMPLATES`:

| Key | Use When | Style |
|-----|----------|-------|
| `comic` | Humor / AI fails / relatable work | 6-panel 3×2 comic, flat vector, speech bubbles ≤8 words |
| `roadmap` | Tech stacks / skills | Dark navy glowing flowchart, 7 glass cards, cyan circuit traces |
| `comparison` | X vs Y (GPU vs TPU) | 4 cards 2×2, color-coded headers, isometric hero, cream grid |
| `sketch_story` | Paradox / narrative | Hand-drawn ink, bridge metaphor, orange/blue highlights |
| `billboard` | Listicles (How to stay poor) | Huge headline + 6 icons 2×3 grid, high contrast |

Prompt rule `post_prompts.py:286`: **every word in the image must come from the caption** — no generic labels. Ends with `crisp vector, 8k, ultra-detailed, perfectly legible English`.

Generation in `linkedin_pipeline.py:325` is your exact snippet:

```python
requests.post(
  "https://api.stability.ai/v2beta/stable-image/generate/ultra",
  headers={"authorization": f"Bearer {STABILITY_API_KEY}", "accept": "image/*"},
  files={"none": ''},
  data={"prompt": full_prompt, "output_format": "webp"},
)
```

---

## 🚀 Quick Start (Local)

```bash
# 1. Clone & install
git clone <your-repo>.git && cd LinkedIn_Post_Automation
pip install -r requirements.txt

# 2. Configure secrets
cp .env.example .env
# Edit .env — set these 5 values:
# GEMINI_API_KEY=...
# STABILITY_AI=sk-...
# LINKEDIN_TOKEN=...          # from linkedin_poster.py auth flow
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
|-----|--------|----------|-------|
| `GEMINI_API_KEY` | https://aistudio.google.com/apikey | ✅ | Gemini 3.6 Flash (`linkedin_pipeline.py:55`) |
| `STABILITY_AI` | https://platform.stability.ai | ✅ | Stability Ultra — free tier limited calls/day. Code reads `STABILITY_AI` (`linkedin_pipeline.py:49`) |
| `LINKEDIN_TOKEN` | `linkedin_poster.py:26` OAuth | ✅ | ~60 days validity |
| `CLIENT_ID` / `CLIENT_SECRET` | LinkedIn Developer Portal | ✅ (once) | For `get_authorization_url()` |

`.env` is git-ignored (`.gitignore:3`). Never commit it.

---

## ☁️ Cloud Scheduling (GitHub Actions)

Runs headless every 2 days — no laptop needed. See `GITHUB_ACTIONS_SETUP.md`.

- Workflow: `.github/workflows/auto_post.yml:14` — `cron: "0 9 */2 * *"` (09:00 UTC), `workflow_dispatch` for manual trigger
- Runner: `ubuntu-latest` + Python `3.12` (`auto_post.yml:27,39`)
- Secrets: `GEMINI_API_KEY`, `LINKEDIN_TOKEN`, `STABILITY_AI` (Repo → Settings → Secrets and variables → Actions)
- Persistence: workflow commits history back to survive ephemeral runners; `_seed_from_history()` (`linkedin_pipeline.py:80`) rehydrates ChromaDB each run

```bash
git init && git add . && git commit -m "Initial commit"
git branch -M main && git remote add origin https://github.com/<you>/<repo>.git
git push -u origin main
# Add 3 secrets in GitHub → watch Actions tab → Run workflow
```

---

## 🎛️ Customization

All prompts & domains live in **`post_prompts.py`** — edit that file only:

- **Topics:** `TOPIC_DOMAINS` (`post_prompts.py:13`) — add RSS `feeds` or `evergreen` strings. Auto-aggregated via `ALL_RSS_FEEDS` / `ALL_EVERGREEN_TOPICS`.
- **Caption style:** `content_prompt()` (`post_prompts.py:216`) — hook, 3-4 punchy paras, 5-7 emojis, woven hashtags, final hashtag line.
- **Critic rules:** `critique_prompt()` / `CLICHES` (`linkedin_pipeline.py:229`) — quality gate `QUALITY_GATE=7` (`linkedin_pipeline.py:59`).
- **Image styles:** `VIRAL_TEMPLATES` (`post_prompts.py:168`) + `image_prompt_gen()` (`post_prompts.py:272`) — add a 6th template by adding a key and a `TEMPLATE F` block.

---

## 🔧 Troubleshooting

| Symptom | Fix |
|---------|-----|
| `GEMINI_API_KEY or LINKEDIN_TOKEN missing` | Check `.env` exists and is loaded (`load_dotenv()`); on Actions check Secrets names match exactly |
| `Stability error 402 / 429` | Free tier quota hit — limited calls/day. Wait or upgrade at platform.stability.ai |
| `429 Gemini` / `model not found` | Pipeline auto-retries with jitter (`_gemini_backoff_wait()` `linkedin_pipeline.py:120`). If persistent, update `GEMINI_MODEL` (`linkedin_pipeline.py:55`) |
| `Not authorized / Invalid token` | LinkedIn token expired (~60d). Re-run `python linkedin_poster.py` and update `.env` + GitHub Secret `LINKEDIN_TOKEN` |
| ChromaDB empty on Actions | Ensure history file is committed (not ignored) — `_seed_from_history()` needs it |
| Image text gibberish | Keep bubble/label fragments ≤8 words (enforced in templates) — Stability renders short text best |

---

> Built with Gemini • Stability AI Ultra • LangGraph • ChromaDB • LinkedIn API v202608 (`linkedin_poster.py:14`)
