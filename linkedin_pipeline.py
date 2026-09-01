"""
LinkedIn Daily Post Automation Pipeline (LangGraph)
Flow: topic (latest fetch + fallback) -> content -> self-critique/revision loop ->
      image prompt -> uniqueness check -> image gen (Cloudflare Workers AI) -> post -> save history

Text generation: Google Gemini (latest Flash model)
Image generation: Cloudflare Workers AI (@cf/black-forest-labs/flux-1-schnell),
                  falls back to @cf/stabilityai/stable-diffusion-xl-base-1.0 on the same account
Uniqueness tracking: local ChromaDB (similarity check against old posts)
Topics: multi-domain (AI, business, career, psychology, science, design, money)
        - free RSS feeds per domain + evergreen lists, defined in post_prompts.py
Prompts & image styles: all centralized in post_prompts.py
Credentials: loaded from a .env file (python-dotenv)
"""

import os
import json
import io
import time
import random
import secrets
import warnings

# Suppress the noisy LangGraph/LangChain pending-deprecation warning about
# `allowed_objects` (harmless internal notice, not something we control) -
# must be set before importing langgraph.graph, which triggers the warning.
warnings.filterwarnings("ignore", message=".*allowed_objects.*")

import requests
import feedparser
import chromadb
from typing import TypedDict
from dotenv import load_dotenv
from langgraph.graph import StateGraph, END
from PIL import Image

# All text prompts, topic domains and infographic style presets live in post_prompts.py
from post_prompts import (
    ALL_RSS_FEEDS,
    ALL_EVERGREEN_TOPICS,
    content_prompt,
    critique_prompt,
    revise_prompt,
    image_prompt_gen,
    image_qa_prompt,
)

# linkedin_poster.py must exist in the same folder; posting functions are imported from there
from linkedin_poster import get_person_urn, register_image_upload, upload_image_binary, create_post

# ---- CONFIG: loaded from the .env file (create .env in this folder) ----
load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
LINKEDIN_TOKEN = os.getenv("LINKEDIN_TOKEN")
CLOUDFLARE_ACCOUNT_ID = os.getenv("CLOUDFLARE_ACCOUNT_ID")
CLOUDFLARE_API_KEY = os.getenv("CLOUDFLARE_API_KEY")

if not CLOUDFLARE_ACCOUNT_ID or not CLOUDFLARE_API_KEY:
    print("[warn] CLOUDFLARE_ACCOUNT_ID / CLOUDFLARE_API_KEY not found in .env - image generation will fail.")


# Gemini models in priority order: flash for quality, lite as quota fallback.

GEMINI_MODELS = ("gemini-3.6-flash", "gemini-3.1-flash-lite")
SIMILARITY_THRESHOLD = 0.85                    # anything above this similarity is treated as duplicate
MAX_RETRIES = 3                                # how many times to retry with a new topic if duplicate found
MAX_REVISIONS = 2                              # how many times the critic loop may revise a weak draft
QUALITY_GATE = 7                               # post must score at least this to skip revision

# ---- Gemini API call settings (free-tier rate-limit protection) ----
MODEL_MAX_RETRIES = 2                          # retries per model before switching to the next model
API_BACKOFF_BASE = 2                           # base seconds for exponential backoff (2, 4, 8, 16…)
API_BACKOFF_MAX = 60                           # cap backoff wait at this many seconds
API_PACE_DELAY = 5.0                           # min seconds between consecutive successful API calls

if not GEMINI_API_KEY or not LINKEDIN_TOKEN:
    raise ValueError("Set GEMINI_API_KEY and LINKEDIN_TOKEN in your .env file (see .env.example)")

# ---- ChromaDB setup — old posts are stored here for uniqueness checking ----
# NOTE: chroma.sqlite3 + .bin files are NOT git-friendly and get lost on the ephemeral GitHub Actions runner.
# posts_history.json (git-friendly) and re-seed ChromaDB from it each startup.
HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "posts_history.json")

chroma_client = chromadb.PersistentClient(path="./post_history_db")
collection = chroma_client.get_or_create_collection("linkedin_posts")


def _seed_from_history():
    """Load plain-text post history and backfill the ChromaDB collection so
    uniqueness checks work on a fresh runner (GitHub Actions)."""
    if not os.path.exists(HISTORY_FILE):
        return 0
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            history = json.load(f)
    except (json.JSONDecodeError, OSError):
        print("[history] could not read posts_history.json - starting fresh")
        return 0
    # Only add docs the collection doesn't already have, avoids duplicates on local runs.
    existing = collection.count()
    new_docs = history[existing:]
    for i, doc in enumerate(new_docs):
        collection.add(documents=[doc], ids=[f"post_{existing + i + 1}"])
    print(f"[history] seeded {len(new_docs)} post(s) into ChromaDB ({collection.count()} total)")
    return len(new_docs)


_seed_from_history()

# All generated/downloaded images are saved inside this folder
IMAGES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "images")
os.makedirs(IMAGES_DIR, exist_ok=True)


class PipelineState(TypedDict):
    topic: str
    post_text: str
    image_prompt: str
    image_path: str
    is_unique: bool
    retry_count: int
    tried_topics: list[str]
    critique_score: int
    critique_feedback: str
    revision_count: int


def _gemini_backoff_wait(resp, attempt: int) -> float:
    """Calculate how long to wait before retrying a failed Gemini API call.

    Honors the ``Retry-After`` header when present; otherwise falls back
    to exponential backoff with jitter (``base * 2^(attempt-1)`` +/- 1s),
    capped at ``API_BACKOFF_MAX``.
    """
    if resp is not None:
        retry_after = resp.headers.get("Retry-After")
        if retry_after:
            try:
                return float(retry_after)
            except ValueError:
                pass  # malformed header -> fall back to exponential backoff
    wait = min(API_BACKOFF_MAX, API_BACKOFF_BASE * (2 ** (attempt - 1)))
    return wait + random.uniform(0, 1)  # jitter


def _gemini_url(model: str) -> str:
    return (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={GEMINI_API_KEY}"
    )


def call_gemini(prompt: str) -> str:
    """Call Gemini for text generation with multi-model quota fallback.

    Tries each model in GEMINI_MODELS in order. Within a model, 429/5xx are
    retried with backoff; if a model's quota is exhausted (429 persists or a
    per-day quota error), it moves on to the next model instead of dying.
    """
    body = {"contents": [{"parts": [{"text": prompt}]}]}
    last_exc = None
    for model in GEMINI_MODELS:
        for attempt in range(1, MODEL_MAX_RETRIES + 1):
            try:
                resp = requests.post(_gemini_url(model), json=body, timeout=60)
                status = resp.status_code

                # --- Rate-limited (429) or transient server errors (5xx) ---
                if status == 429 or status >= 500:
                    print(f"[gemini] {model} HTTP {status} body: {resp.text[:200]}")
                    if attempt < MODEL_MAX_RETRIES:
                        wait = _gemini_backoff_wait(resp, attempt)
                        print(f"[gemini] backing off {wait:.1f}s (attempt {attempt}/{MODEL_MAX_RETRIES})")
                        time.sleep(wait)
                        continue
                    print(f"[gemini] {model} quota exhausted - switching model")
                    break  # move to next model

                # --- Non-retryable client errors (400, 401, 403) -> fail fast ---
                resp.raise_for_status()
                result = resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
                time.sleep(API_PACE_DELAY)  # stay under free-tier RPM
                return result

            except (requests.exceptions.ConnectionError,
                    requests.exceptions.Timeout) as exc:
                last_exc = exc
                wait = _gemini_backoff_wait(None, attempt)
                print(f"[gemini] {model} network error ({type(exc).__name__}), "
                      f"backing off {wait:.1f}s (attempt {attempt}/{MODEL_MAX_RETRIES})")
                time.sleep(wait)

    raise RuntimeError(
        "Gemini API failed on all models (GEMINI_MODELS) - see printed "
        "response bodies above for the exact quota that was hit"
    ) from last_exc


def call_gemini_vision(image_bytes: bytes, mime_type: str, prompt: str) -> str:
    """Send an image + text prompt to Gemini and return the text reply.

    Used for the image spelling/legibility QA check. Same multi-model
    quota fallback as call_gemini(); QA is best-effort.
    """
    import base64
    body = {
        "contents": [{
            "parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": mime_type, "data": base64.b64encode(image_bytes).decode()}},
            ]
        }],
        "generationConfig": {"temperature": 0.0},
    }
    for model in GEMINI_MODELS:
        for attempt in range(1, MODEL_MAX_RETRIES + 1):
            try:
                resp = requests.post(_gemini_url(model), json=body, timeout=90)
                if resp.status_code == 429 or resp.status_code >= 500:
                    if attempt < MODEL_MAX_RETRIES:
                        time.sleep(_gemini_backoff_wait(resp, attempt))
                        continue
                    break  # next model
                resp.raise_for_status()
                return resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
            except (requests.exceptions.ConnectionError,
                    requests.exceptions.Timeout):
                time.sleep(_gemini_backoff_wait(None, attempt))
    # QA is best-effort - on repeated failure accept the image rather than kill the run
    return "VERDICT: OK"


def check_image_spelling(image_bytes: bytes, mime_type: str) -> tuple[bool, str]:
    """Returns (ok, issues). ok=True means visible text is spelled, readable, and grammatical."""
    try:
        reply = call_gemini_vision(image_bytes, mime_type, image_qa_prompt())
    except Exception as e:
        print(f"[image-qa] vision check failed ({e}) - accepting image")
        return True, ""
    upper = reply.upper()
    # Fail-closed: accept ONLY an explicit VERDICT: OK. A missing, garbled or
    # unexpected verdict is treated as BAD so the image gets re-rendered.
    ok = "VERDICT: OK" in upper and "VERDICT: BAD" not in upper
    issues = ""
    for line in reply.splitlines():
        stripped = line.strip().upper()
        if stripped.startswith("ISSUES:"):
            issues = line.split(":", 1)[1].strip()
            if issues.lower() == "none":
                issues = ""
        elif stripped.startswith("TEXTS:") and not issues:
            # fall back to the transcription when ISSUES: is missing/empty
            transcription = line.split(":", 1)[1].strip()
            if transcription and transcription.lower() != "none":
                issues = f"check these fragments: {transcription}"
    return ok, issues



def get_topic_pool() -> list[str]:
    """Combine live headlines from ALL domain RSS feeds with the evergreen lists into one pool"""
    pool = list(ALL_EVERGREEN_TOPICS)  # static fallback is always included
    for feed_url in ALL_RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url, request_headers={"User-Agent": "Mozilla/5.0"})
            pool.extend(entry.title for entry in feed.entries[:5])
        except Exception:
            continue  # if one feed fails, try the others; don't break the whole pipeline
    return pool


# ---- Node 1: Pick a topic (live news from any domain first, static fallback list) ----
def pick_topic(state: PipelineState) -> PipelineState:
    tried = state.get("tried_topics", [])
    pool = [t for t in get_topic_pool() if t not in tried]
    if not pool:
        pool = ALL_EVERGREEN_TOPICS  # once everything has been tried, pick from evergreen lists
    topic = secrets.choice(pool)  # crypto-safe pick 
    print(f"[topic] picked ({len(pool)} available): {topic}")
    state["topic"] = topic
    state["tried_topics"] = tried + [topic]
    state["retry_count"] = state.get("retry_count", 0)
    return state


# ---- Node 2: Write the post text (strong creative caption with hashtags woven in) ----
def generate_content(state: PipelineState) -> PipelineState:
    state["post_text"] = call_gemini(content_prompt(state["topic"]))
    state["revision_count"] = state.get("revision_count", 0)
    return state


# ---- Node 2b: Agentic self-critique - the post scores and reviews itself ----
CLICHES = [
    "game-changer", "game changer", "in today's fast-paced", "in today's world",
    "delve into", "unlock the power", "revolutionize", "seamlessly integrate",
    "in the realm of", "tapestry", "navigate the landscape", "supercharge",
]

def critique_post(state: PipelineState) -> PipelineState:
    text = state["post_text"]
    raw = call_gemini(critique_prompt(text))
    score, feedback = 5, ""
    in_feedback = False
    for line in raw.splitlines():
        stripped = line.strip()
        upper = stripped.upper()
        if upper.startswith("SCORE:"):
            digits = "".join(c for c in stripped.split(":", 1)[1] if c.isdigit())
            score = min(10, int(digits)) if digits else 5
            in_feedback = False
        elif upper.startswith("FEEDBACK:"):
            feedback = stripped.split(":", 1)[1].strip()
            in_feedback = True  # feedback may continue on following lines - keep it all
        elif in_feedback and stripped:
            feedback += " " + stripped

    # Deterministic penalty: known AI cliches instantly cap the score below the revision gate
    lower = text.lower()
    hits = [c for c in CLICHES if c in lower]
    if hits:
        score = min(score, 5)
        feedback = f"Remove these overused AI phrases: {', '.join(hits)}. " + (feedback or "")
    # Deterministic penalty: missing final hashtag line means the format contract was broken
    if not any(word.startswith("#") for word in text.split()[-8:]):
        score = min(score, 5)
        feedback = "The mandatory final hashtag line is missing. " + (feedback or "")
    # Deterministic penalty: too few emojis -> flat caption. Count via the unicode emoji range.
    emoji_count = sum(1 for ch in text if ord(ch) > 0x2190)
    if emoji_count < 4:
        score = min(score, 6)
        feedback = (
            f"Only {emoji_count} emojis found - add 5-7 expressive ones (one per paragraph) "
            "to give the post energy. " + (feedback or "")
        )

    state["critique_score"] = score
    state["critique_feedback"] = feedback.strip()
    print(f"[critic] score={score}/10 | feedback={state['critique_feedback'][:120]}")
    return state


def route_after_critique(state: PipelineState) -> str:
    """Move on if the score is good or max revisions are used up, otherwise revise it"""
    if state["critique_score"] >= QUALITY_GATE or state["revision_count"] >= MAX_REVISIONS:
        print(f"[critic] proceeding with score={state['critique_score']} "
              f"(revisions used: {state['revision_count']}/{MAX_REVISIONS})")
        return "generate_image_prompt"
    state["revision_count"] += 1
    return "revise_content"


# ---- Node 2c: Improve the post using the critic's feedback ----
def revise_content(state: PipelineState) -> PipelineState:
    print(f"[reviser] applying revision {state['revision_count']}/{MAX_REVISIONS}...")
    state["post_text"] = call_gemini(
        revise_prompt(state["post_text"], state["critique_feedback"])
    )
    return state


# ---- Node 3: Generate image prompt (agent-driven, 7 templates) ----
def generate_image_prompt(state: PipelineState) -> PipelineState:
    state["image_prompt"] = call_gemini(image_prompt_gen(state["post_text"]))
    print(f"[image-prompt] {state['image_prompt'][:160]}...")
    return state


# ---- Node 4: Uniqueness check — compare similarity against old posts ----
def check_uniqueness(state: PipelineState) -> PipelineState:
    if collection.count() == 0:
        state["is_unique"] = True  # first post is always unique
        return state
    results = collection.query(query_texts=[state["post_text"]], n_results=1)
    distance = results["distances"][0][0]
    similarity = 1 - distance  # smaller distance = more similar
    state["is_unique"] = similarity < SIMILARITY_THRESHOLD
    return state


def route_after_uniqueness(state: PipelineState) -> str:
    """If a duplicate is found, retry with a new topic (up to max retries), else move on to the image step"""
    if state["is_unique"] or state["retry_count"] >= MAX_RETRIES:
        return "generate_image"
    state["retry_count"] += 1
    return "pick_topic"


# ---- Image generation via Cloudflare Workers AI (REST endpoint) ----
JPEG_MIME = "image/jpeg"  # default MIME for all Workers AI image responses (SonarQube S1192)
CF_FLUX2_KLEIN = "@cf/black-forest-labs/flux-2-klein-4b"  # ultra-fast distilled FLUX.2 (default)
CF_FLUX2_DEV = "@cf/black-forest-labs/flux-2-dev"        # higher quality, slower fallback
CF_FLUX1_SCHNELL = "@cf/black-forest-labs/flux-1-schnell"  # fast fallback
CF_SDXL_MODEL = "@cf/stabilityai/stable-diffusion-xl-base-1.0"  # last resort


def _run_cf_model(model: str, prompt: str) -> tuple[bytes, str]:
    """Call one Cloudflare Workers AI image model; returns (image_bytes, content_type).

    Raises RuntimeError with the API error message on any non-200 response.
    """
    import base64
    url = (
        f"https://api.cloudflare.com/client/v4/accounts/{CLOUDFLARE_ACCOUNT_ID}"
        f"/ai/run/{model}"
    )
    if model in (CF_FLUX2_KLEIN, CF_FLUX2_DEV):
        # FLUX.2 models require multipart/form-data and return JSON {result: {image: base64}}
        resp = requests.post(
            url,
            headers={"Authorization": f"Bearer {CLOUDFLARE_API_KEY}"},
            files={
                "prompt": (None, prompt),
                "steps": (None, "8"),
                "width": (None, "1024"),
                "height": (None, "1024"),
                "guidance": (None, "3.5"),
                "seed": (None, str(secrets.randbelow(2_147_483_647))),  # crypto-safe seed (SonarQube S2245)
            },
            timeout=180,
        )
    elif model == CF_FLUX1_SCHNELL:
        # flux-1-schnell's JSON schema only accepts prompt + steps; returns raw image bytes
        resp = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {CLOUDFLARE_API_KEY}",
                "Content-Type": "application/json",
            },
            json={"prompt": prompt, "steps": 8},
            timeout=180,
        )
    else:
        resp = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {CLOUDFLARE_API_KEY}",
                "Content-Type": "application/json",
            },
            json={"prompt": prompt},
            timeout=180,
        )
    if resp.status_code != 200:
        try:
            detail = resp.json().get("errors", resp.text[:300])
        except Exception:
            detail = resp.text[:300]
        raise RuntimeError(f"Cloudflare {model} error {resp.status_code}: {detail}")
    content_type = resp.headers.get("Content-Type", JPEG_MIME)
    if "image" in content_type:
        return resp.content, content_type
    # JSON-wrapped result: {"result": {"image": "<base64>"}}
    try:
        payload = resp.json()
        b64 = (payload.get("result") or {}).get("image")
        if b64:
            return base64.b64decode(b64), JPEG_MIME
    except Exception:
        pass
    raise RuntimeError(f"Cloudflare {model} returned non-image response: {content_type}")


# ---- Node 6: Generate the image - Cloudflare Workers AI (klein-4b, then dev/schnell/SDXL) ----
IMAGE_QA_MAX_ATTEMPTS = 3   # normal -> fewer labels -> text-free (spelling-proof, neuron-friendly)


def _generate_via_models(prompt: str, errors: list, attempt: int = 1) -> tuple | None:
    """Try Cloudflare models in order; return (bytes, content_type) or None.

    Attempt 1 uses the cheap-first chain (klein-4b). On QA-failed retries we
    lead with the BEST text-rendering model (flux-2-dev) so the re-render has
    the strongest chance of spelling the text correctly.
    """
    if attempt >= 2:
        models = (CF_FLUX2_DEV, CF_FLUX2_KLEIN, CF_FLUX1_SCHNELL, CF_SDXL_MODEL)
    else:
        models = (CF_FLUX2_KLEIN, CF_FLUX2_DEV, CF_FLUX1_SCHNELL, CF_SDXL_MODEL)
    for model in models:
        try:
            return _run_cf_model(model, prompt)
        except Exception as e:
            errors.append(str(e))
            print(f"[image] {e} - trying next model...")
    return None


def _build_retry_prompt(full_prompt: str, issues: str, attempt: int) -> str:
    """Escalating re-render prompt after a QA failure - attempt 3 is text-free,
    which makes spelling mistakes physically possible to avoid while saving neurons."""
    if attempt >= 3:
        # Last resort: strip ALL text - typography is where image models fail most
        return (
            f"{full_prompt}\n\nCRITICAL: previous renders kept misspelling or garbling "
            f"text ({issues}). Render the SAME visual concept with ABSOLUTELY NO text, "
            f"letters, numbers, words, signs or captions anywhere in the image. Use only "
            f"icons, symbols, shapes, arrows, charts and illustrations to convey the idea. "
            f"An image with zero characters cannot have typos - enforce zero characters."
        )
    max_labels = 3 if attempt == 2 else 4
    return (
        f"{full_prompt}\n\nIMPORTANT: previous render contained misspelled, garbled, "
        f"duplicated or invented text ({issues}). Re-render with AT MOST {max_labels} "
        f"DIFFERENT text fragments (1-3 words each, no duplicates), spell every quoted "
        f"word letter-for-letter correctly using common, simple English words only. "
        f"Prefer icons and illustrations over text."
    )


def _normalize_to_png(image_bytes: bytes, content_type: str) -> tuple[bytes, str]:
    """Return PNG bytes for cleaner text QA and final upload quality."""
    if "png" in (content_type or "").lower():
        return image_bytes, "image/png"
    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            out = io.BytesIO()
            img.save(out, format="PNG")
            return out.getvalue(), "image/png"
    except Exception:
        return image_bytes, content_type


def _render_best_image(full_prompt: str, errors: list) -> tuple | None:
    """Generate + Gemini QA up to IMAGE_QA_MAX_ATTEMPTS; keep the best render."""
    best = None
    prompt = full_prompt
    for attempt in range(1, IMAGE_QA_MAX_ATTEMPTS + 1):
        generated = _generate_via_models(prompt, errors, attempt)
        if generated is None:
            return best
        img, ctype = generated
        qa_img, qa_ctype = _normalize_to_png(img, ctype)
        ok, issues = check_image_spelling(qa_img, qa_ctype)
        model_used = "flux-2-dev (best)" if attempt >= 2 else "flux-2-klein-4b (fast)"
        print(f"[image-qa] attempt {attempt} [{model_used}]: {'OK' if ok else 'BAD text'} "
              f"{('- ' + issues) if issues else ''}")
        if ok:
            return qa_img, qa_ctype
        best = (qa_img, qa_ctype)  # keep the latest failed attempt as fallback
        prompt = _build_retry_prompt(full_prompt, issues, attempt + 1)
    return best


def generate_image(state: PipelineState) -> PipelineState:
    full_prompt = state["image_prompt"].strip()
    image_bytes = None
    ext = "jpg"
    errors = []

    if CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_KEY:
        print(f"[image] Cloudflare Workers AI ({CF_FLUX2_KLEIN}) | prompt: {full_prompt[:200]}...")
        best = _render_best_image(full_prompt, errors)
        if best:
            image_bytes, content_type = best
            image_bytes, content_type = _normalize_to_png(image_bytes, content_type)
            ext = "png" if "png" in content_type else "jpg"
    else:
        errors.append("CLOUDFLARE_ACCOUNT_ID / CLOUDFLARE_API_KEY missing from .env")

    if image_bytes is None:
        raise RuntimeError(f"Image generation failed on all Cloudflare models: {'; '.join(errors)}")

    image_path = os.path.join(IMAGES_DIR, f"post_image_{int(time.time())}.{ext}")
    with open(image_path, 'wb') as file:
        file.write(image_bytes)
    print(f"[image] saved {len(image_bytes)//1024}KB -> {image_path}")
    state["image_path"] = image_path
    return state


# ---- Node 7: Publish the actual post to LinkedIn ----
def post_to_linkedin(state: PipelineState) -> PipelineState:
    person_urn = get_person_urn(LINKEDIN_TOKEN)
    upload_url, image_urn = register_image_upload(LINKEDIN_TOKEN, person_urn)
    upload_image_binary(upload_url, state["image_path"], LINKEDIN_TOKEN)
    post_id = create_post(LINKEDIN_TOKEN, person_urn, state["post_text"], image_urn)
    print("Posted successfully:", post_id)
    return state


# ---- Node 8: Save history (plain text JSON + ChromaDB, for duplicate detection next time) ----
def save_history(state: PipelineState) -> PipelineState:
    # Append to the plain-text history file (git-friendly; survives GitHub Actions runs).
    history = []
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                history = json.load(f)
        except (json.JSONDecodeError, OSError):
            history = []
    history.append(state["post_text"])
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False)

    # Also add to the ChromaDB collection (used live for uniqueness within this run).
    collection.add(
        documents=[state["post_text"]],
        ids=[f"post_{collection.count() + 1}"],
    )
    print(f"[history] saved {len(history)} posts to posts_history.json")
    return state


# ---- Build the graph ----
graph = StateGraph(PipelineState)
graph.add_node("pick_topic", pick_topic)
graph.add_node("generate_content", generate_content)
graph.add_node("critique_post", critique_post)
graph.add_node("revise_content", revise_content)
graph.add_node("generate_image_prompt", generate_image_prompt)
graph.add_node("check_uniqueness", check_uniqueness)
graph.add_node("generate_image", generate_image)
graph.add_node("post_to_linkedin", post_to_linkedin)
graph.add_node("save_history", save_history)

graph.set_entry_point("pick_topic")
graph.add_edge("pick_topic", "generate_content")
graph.add_edge("generate_content", "critique_post")
graph.add_conditional_edges(
    "critique_post",
    route_after_critique,
    {"generate_image_prompt": "generate_image_prompt", "revise_content": "revise_content"},
)
graph.add_edge("revise_content", "critique_post")  # revised draft goes back through the critic
graph.add_edge("generate_image_prompt", "check_uniqueness")
graph.add_conditional_edges(
    "check_uniqueness",
    route_after_uniqueness,
    {"generate_image": "generate_image", "pick_topic": "pick_topic"},
)
graph.add_edge("generate_image", "post_to_linkedin")
graph.add_edge("post_to_linkedin", "save_history")
graph.add_edge("save_history", END)

app = graph.compile()


if __name__ == "__main__":
    app.invoke(
        {
            "topic": "",
            "post_text": "",
            "image_prompt": "",
            "image_path": "",
            "is_unique": False,
            "retry_count": 0,
            "tried_topics": [],
            "critique_score": 0,
            "critique_feedback": "",
            "revision_count": 0,
        }
    )