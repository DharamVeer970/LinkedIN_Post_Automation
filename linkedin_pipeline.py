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
import re
import time
import random
import secrets
import unicodedata
import warnings

# Suppress LangGraph/LangChain deprecation warnings
warnings.simplefilter("ignore")

# Import and suppress the specific LangChain warning class
try:
    from langchain._api import LangChainPendingDeprecationWarning
    warnings.filterwarnings("ignore", category=LangChainPendingDeprecationWarning)
except ImportError:
    try:
        from langchain_core._api import LangChainPendingDeprecationWarning
        warnings.filterwarnings("ignore", category=LangChainPendingDeprecationWarning)
    except ImportError:
        pass

import requests
import feedparser
import chromadb
from typing import TypedDict
from dotenv import load_dotenv
from langgraph.graph import StateGraph, END
from PIL import Image, ImageDraw, ImageFont

# Re-enable warnings after imports
warnings.simplefilter("default")

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

# ---- Shared constants (Sonar S1192) ----
IMAGE_PNG_MIME = "image/png"

# Pre-compiled, linear regexes for image-prompt text extraction (Sonar S8786)
# Linear - no nested/optional quantifiers; matches `exact text: "X"` from post_prompts.py
_EXACT_TEXT_RE = re.compile(r'exact text: "([^"]+)"')
_CLEAN_TEXT_RE = re.compile(r' exact text: "[^"]*"')

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


def _build_gemini_body(prompt: str, image_bytes: bytes | None) -> dict:
    """Build Gemini request body."""
    import base64
    body: dict = {"contents": [{"parts": [{"text": prompt}]}]}
    if image_bytes is None:
        return body
    body["contents"][0]["parts"].append(
        {"inline_data": {"mime_type": IMAGE_PNG_MIME, "data": base64.b64encode(image_bytes).decode()}}
    )
    body["generationConfig"] = {"temperature": 0.0}
    return body


def _is_retryable_status(status: int) -> bool:
    return status == 429 or status >= 500


def _backoff_delay(attempt: int) -> float:
    return min(API_BACKOFF_MAX, API_BACKOFF_BASE * (2 ** (attempt - 1))) + random.uniform(0, 1)


def _call_single_model(model: str, body: dict, image_bytes: bytes | None) -> str | None:
    """Try one model with retries. Returns text on success, None on quota/network failure."""
    for attempt in range(1, MODEL_MAX_RETRIES + 1):
        try:
            url = (
                f"https://generativelanguage.googleapis.com/v1beta/models/"
                f"{model}:generateContent?key={GEMINI_API_KEY}"
            )
            resp = requests.post(url, json=body, timeout=90 if image_bytes else 60)
            if _is_retryable_status(resp.status_code):
                if attempt < MODEL_MAX_RETRIES:
                    wait = _backoff_delay(attempt)
                    print(f"[gemini] {model} HTTP {resp.status_code}, backing off {wait:.1f}s")
                    time.sleep(wait)
                    continue
                print(f"[gemini] {model} quota exhausted - switching model")
                return None
            resp.raise_for_status()
            result = resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
            time.sleep(API_PACE_DELAY)
            return result
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
            wait = _backoff_delay(attempt)
            print(f"[gemini] {model} network error ({type(exc).__name__}), backing off {wait:.1f}s")
            time.sleep(wait)
    return None


def call_gemini(prompt: str, image_bytes: bytes = None) -> str:
    """Call Gemini for text or vision generation with multi-model quota fallback."""
    body = _build_gemini_body(prompt, image_bytes)
    for model in GEMINI_MODELS:
        result = _call_single_model(model, body, image_bytes)
        if result is not None:
            return result
    raise RuntimeError("Gemini API failed on all models")


def _parse_qa_issues(reply: str) -> str:
    """Extract ISSUES line from QA reply."""
    for line in reply.splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("ISSUES:"):
            issues = stripped.split(":", 1)[1].strip()
            return "" if issues.lower() == "none" else issues
    return ""


def check_image_spelling(image_bytes: bytes) -> tuple[bool, str]:
    """Returns (ok, issues). ok=True means visible text is spelled correctly."""
    try:
        reply = call_gemini(image_qa_prompt(), image_bytes)
    except Exception as exc:
        return False, f"vision QA unavailable: {exc}"
    upper = reply.upper()
    ok = "VERDICT: OK" in upper and "VERDICT: BAD" not in upper
    return ok, _parse_qa_issues(reply)



def _fetch_feed_titles(feed_url: str) -> list[str]:
    """Fetch one RSS feed with timeout, return up to 5 titles."""
    try:
        resp = requests.get(feed_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
        return [entry.title for entry in feed.entries[:5]]
    except Exception:
        return []


def get_topic_pool() -> list[str]:
    """Combine live headlines from ALL domain RSS feeds with the evergreen lists into one pool"""
    pool = list(ALL_EVERGREEN_TOPICS)
    for feed_url in ALL_RSS_FEEDS:
        pool.extend(_fetch_feed_titles(feed_url))
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

def _parse_critique_response(raw: str) -> tuple[int, str]:
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
            in_feedback = True
        elif in_feedback and stripped:
            feedback += " " + stripped
    return score, feedback


def _apply_cliche_penalty(text: str, score: int, feedback: str) -> tuple[int, str]:
    hits = [c for c in CLICHES if c in text.lower()]
    if not hits:
        return score, feedback
    return min(score, 5), f"Remove these overused AI phrases: {', '.join(hits)}. " + (feedback or "")


def _apply_hashtag_penalty(text: str, score: int, feedback: str) -> tuple[int, str]:
    if any(word.startswith("#") for word in text.split()[-8:]):
        return score, feedback
    return min(score, 5), "The mandatory final hashtag line is missing. " + (feedback or "")


def _apply_emoji_penalty(text: str, score: int, feedback: str) -> tuple[int, str]:
    emoji_count = sum(1 for ch in text if unicodedata.category(ch) == "So")
    if emoji_count >= 4:
        return score, feedback
    msg = (
        f"Only {emoji_count} emojis found - add 5-7 expressive ones (one per paragraph) "
        "to give the post energy. " + (feedback or "")
    )
    return min(score, 6), msg


def critique_post(state: PipelineState) -> PipelineState:
    text = state["post_text"]
    raw = call_gemini(critique_prompt(text))
    score, feedback = _parse_critique_response(raw)
    score, feedback = _apply_cliche_penalty(text, score, feedback)
    score, feedback = _apply_hashtag_penalty(text, score, feedback)
    score, feedback = _apply_emoji_penalty(text, score, feedback)
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


# ---- Image generation via Cloudflare Workers AI ----
JPEG_MIME = "image/jpeg"
IMAGE_QA_MAX_ATTEMPTS = 3

CF_MODELS = {
    "@cf/black-forest-labs/flux-2-klein-4b": {"steps": 8, "width": 1024, "height": 1024, "guidance": 3.5},
    "@cf/black-forest-labs/flux-2-dev": {"steps": 8, "width": 1024, "height": 1024, "guidance": 3.5},
    "@cf/black-forest-labs/flux-1-schnell": {"steps": 8},
    "@cf/stabilityai/stable-diffusion-xl-base-1.0": {},
}


def _run_cf_model(model: str, prompt: str) -> tuple[bytes, str]:
    """Call one Cloudflare Workers AI image model."""
    import base64
    url = f"https://api.cloudflare.com/client/v4/accounts/{CLOUDFLARE_ACCOUNT_ID}/ai/run/{model}"
    params = CF_MODELS.get(model, {})
    is_flux2 = "flux-2" in model

    if is_flux2:
        resp = requests.post(url, headers={"Authorization": f"Bearer {CLOUDFLARE_API_KEY}"},
                             files={"prompt": (None, prompt), **{k: (None, str(v)) for k, v in params.items()}})
    else:
        resp = requests.post(url, headers={"Authorization": f"Bearer {CLOUDFLARE_API_KEY}",
                                           "Content-Type": "application/json"},
                             json={"prompt": prompt, **params})

    if resp.status_code != 200:
        raise RuntimeError(f"Cloudflare {model} error {resp.status_code}: {resp.text[:300]}")

    content_type = resp.headers.get("Content-Type", JPEG_MIME)
    if "image" in content_type:
        return resp.content, content_type
    b64 = (resp.json().get("result") or {}).get("image")
    if b64:
        return base64.b64decode(b64), JPEG_MIME
    raise RuntimeError(f"Cloudflare {model} returned non-image: {content_type}")


def _normalize_to_png(img: bytes, ctype: str) -> tuple[bytes, str]:
    """Ensure image bytes are PNG."""
    if "png" in ctype:
        return img, ctype
    with Image.open(io.BytesIO(img)) as im:
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        return buf.getvalue(), IMAGE_PNG_MIME


def _log_qa_result(attempt: int, ok: bool, issues: str) -> None:
    label = "flux-2-dev" if attempt >= 2 else "flux-2-klein"
    status = "OK" if ok else "BAD"
    suffix = f" - {issues}" if issues else ""
    print(f"[image-qa] attempt {attempt} [{label}]: {status}{suffix}")


def _try_single_model_qa(model: str, prompt: str, attempt: int, errors: list) -> tuple[bytes, str, bool] | None:
    """Try one model, run QA. Returns (bytes, ctype, is_ok) or None on network error."""
    try:
        img, ctype = _run_cf_model(model, prompt)
    except Exception as exc:
        errors.append(str(exc))
        print(f"[image] {exc} - trying next model...")
        return None
    img, ctype = _normalize_to_png(img, ctype)
    ok, issues = check_image_spelling(img)
    _log_qa_result(attempt, ok, issues)
    if not ok:
        errors.append(f"{model}: {issues}")
    return img, ctype, ok


def _generate_image_with_qa(prompt: str, errors: list) -> tuple | None:
    """Try Cloudflare models with QA check. Returns best render, fallback to BAD if needed."""
    models = ("@cf/black-forest-labs/flux-2-klein-4b", "@cf/black-forest-labs/flux-2-dev",
              "@cf/black-forest-labs/flux-1-schnell", "@cf/stabilityai/stable-diffusion-xl-base-1.0")
    best: tuple[bytes, str] | None = None
    for attempt in range(1, IMAGE_QA_MAX_ATTEMPTS + 1):
        for model in models:
            result = _try_single_model_qa(model, prompt, attempt, errors)
            if result is None:
                continue  # network error, try next model
            img, ctype, is_ok = result
            if is_ok:
                return img, ctype
            # QA failed but we have a BAD image - keep as fallback and try next attempt
            best = (img, ctype)
            break  # BAD spelling, try next attempt with fresh generation
    if best is not None:
        print(f"[image-qa] all {IMAGE_QA_MAX_ATTEMPTS} attempts had spelling issues - using best BAD render as fallback")
    return best


def _extract_text_and_clean_prompt(image_prompt: str) -> tuple[dict, str]:
    """Extract text elements from prompt and return a text-free version."""
    matches = _EXACT_TEXT_RE.findall(image_prompt)
    elements = {"headline": matches[0] if matches else "", "labels": matches[1:]}
    clean = _CLEAN_TEXT_RE.sub("", image_prompt)
    clean += "\n\nNO TEXT: render only icons/visuals, leave blank spaces for text overlay."
    return elements, clean.strip()


def _load_font(size: int):
    """Load a truetype font or fallback to default."""
    for path in ["C:/Windows/Fonts/arialbd.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]:
        try:
            return ImageFont.truetype(path, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def _draw_outlined_text(draw: ImageDraw.ImageDraw, x: int, y: int, text: str, font) -> None:
    for dx, dy in [(-2, 0), (2, 0), (0, -2), (0, 2)]:
        draw.text((x + dx, y + dy), text, font=font, fill="black")
    draw.text((x, y), text, font=font, fill="white")


def _draw_headline(draw: ImageDraw.ImageDraw, w: int, h: int, headline: str, font) -> None:
    text = headline.upper()
    bbox = draw.textbbox((0, 0), text, font=font)
    x = (w - (bbox[2] - bbox[0])) // 2
    y = int(h * 0.06)
    _draw_outlined_text(draw, x, y, text, font)


def _draw_labels(draw: ImageDraw.ImageDraw, w: int, h: int, labels: list[str], font) -> None:
    small_font = _load_font(max(16, int(w * 0.03)))
    # fallback to main font if default was returned and small font failed (already handled)
    count = len(labels)
    for i, label in enumerate(labels[:6]):
        bbox = draw.textbbox((0, 0), label, font=small_font)
        x = (w - (bbox[2] - bbox[0])) // 2
        y = int(h * (0.4 + 0.4 * i / max(count, 1)))
        _draw_outlined_text(draw, x, y, label, small_font)


def _overlay_text(image_bytes: bytes, elements: dict) -> bytes:
    """Overlay headline and labels onto image using PIL."""
    img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    draw = ImageDraw.Draw(img)
    w, h = img.size
    font = _load_font(max(28, int(w * 0.055)))
    if elements["headline"]:
        _draw_headline(draw, w, h, elements["headline"], font)
    if elements["labels"]:
        _draw_labels(draw, w, h, elements["labels"], font)
    out = io.BytesIO()
    img.convert("RGB").save(out, format="PNG")
    return out.getvalue()

def _try_apply_overlay(image_bytes: bytes, content_type: str, ext: str, elements: dict) -> tuple[bytes, str, str]:
    if not (elements["headline"] or elements["labels"]):
        return image_bytes, content_type, ext
    try:
        new_bytes = _overlay_text(image_bytes, elements)
        print("[image] text overlay applied")
        return new_bytes, IMAGE_PNG_MIME, "png"
    except Exception as exc:
        print(f"[image] overlay failed ({exc})")
        return image_bytes, content_type, ext


def _ensure_png_output(image_bytes: bytes, content_type: str, ext: str) -> tuple[bytes, str, str]:
    if "png" in content_type:
        return image_bytes, content_type, ext
    new_bytes, new_ctype = _normalize_to_png(image_bytes, content_type)
    return new_bytes, new_ctype, "png"


def _create_placeholder_image() -> bytes:
    """Create a neutral 1024x1024 placeholder when all Cloudflare attempts fail."""
    img = Image.new("RGB", (1024, 1024), color=(245, 245, 240))
    draw = ImageDraw.Draw(img)
    # subtle grid to keep infographic feel even without model
    for i in range(0, 1024, 128):
        draw.line([(i, 0), (i, 1024)], fill=(230, 230, 225), width=1)
        draw.line([(0, i), (1024, i)], fill=(230, 230, 225), width=1)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _create_cloudflare_image(clean_prompt: str, elements: dict, errors: list) -> tuple[bytes | None, str]:
    best = _generate_image_with_qa(clean_prompt, errors)
    if not best:
        return None, "jpg"
    image_bytes, content_type = best
    ext = "jpg"
    image_bytes, content_type, ext = _try_apply_overlay(image_bytes, content_type, ext, elements)
    image_bytes, content_type, ext = _ensure_png_output(image_bytes, content_type, ext)
    return image_bytes, ext


def generate_image(state: PipelineState) -> PipelineState:
    full_prompt = state["image_prompt"].strip()
    errors: list[str] = []
    if not CLOUDFLARE_ACCOUNT_ID or not CLOUDFLARE_API_KEY:
        errors.append("CLOUDFLARE_ACCOUNT_ID / CLOUDFLARE_API_KEY missing from .env")
        raise RuntimeError(f"Image generation failed: {'; '.join(errors)}")
    elements, clean_prompt = _extract_text_and_clean_prompt(full_prompt)
    print(f"[image] Cloudflare Workers AI | prompt: {clean_prompt[:200]}...")
    print(f"[image] overlay: \"{elements['headline']}\" + {len(elements['labels'])} labels")
    image_bytes, ext = _create_cloudflare_image(clean_prompt, elements, errors)
    if image_bytes is None:
        # ultimate fallback: don't crash pipeline, post placeholder with overlay
        print(f"[image] all Cloudflare attempts failed ({'; '.join(errors)[:200]}) - using placeholder")
        image_bytes = _create_placeholder_image()
        try:
            image_bytes = _overlay_text(image_bytes, elements)
        except Exception as exc:
            print(f"[image] placeholder overlay failed ({exc})")
        ext = "png"
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
    history = []
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                history = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    history.append(state["post_text"])
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False)
    collection.add(documents=[state["post_text"]], ids=[f"post_{collection.count() + 1}"])
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