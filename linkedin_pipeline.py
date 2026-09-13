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


# ---- Node 3: Generate image prompt (agent-driven, 15 templates) ----
def _has_overlay_text(image_prompt: str) -> bool:
    """True if Gemini obeyed the format and emitted at least one exact text fragment."""
    return bool(_EXACT_TEXT_RE.search(image_prompt or ""))


def _fallback_elements(post_text: str, topic: str) -> dict:
    """Build overlay text from the post itself when Gemini forgets exact text fragments.

    Guarantees the image never ships with 0 overlay labels (the roadmap run that
    posted pure diffusion-baked text). Headline = topic, labels = first short
    phrases from the post body.
    """
    headline = (topic or "").strip().strip("\"'")[:42]
    labels: list[str] = []
    seen: set[str] = set()
    for line in (post_text or "").splitlines():
        chunk = re.sub(r"[#@*_`>~\-–—\d.)(\[\]]+", " ", line).strip()
        chunk = re.sub(r"\s+", " ", chunk).strip()
        words = chunk.split()
        if len(words) < 2 or len(words) > 5:
            continue
        if len(chunk) > 32 or chunk.lower() in seen:
            continue
        if any(c in chunk for c in ["http", "://", "&", "|"]):
            continue
        seen.add(chunk.lower())
        labels.append(chunk)
        if len(labels) >= 4:
            break
    if not headline and labels:
        headline, labels = labels[0], labels[1:]
    return _sanitize_elements({"headline": headline, "labels": labels})


def generate_image_prompt(state: PipelineState) -> PipelineState:
    prompt = call_gemini(image_prompt_gen(state["post_text"]))
    if not _has_overlay_text(prompt):
        # Gemini echoed "TEMPLATE B - ROADMAP: ..." instead of the final prompt
        # (seen in live run) -> one retry forces the exact-text format.
        print("[image-prompt] no exact text fragments found, retrying once for format...")
        prompt = call_gemini(
            image_prompt_gen(state["post_text"])
            + "\n\nSTRICT: start directly with the visual description. "
            'Never write TEMPLATE, never explain. Emit at least 2 fragments as exact text: "Words".'
        )
    state["image_prompt"] = prompt
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


def _log_qa_result(attempt: int, model: str, ok: bool, issues: str) -> None:
    short = model.split("/")[-1] if "/" in model else model
    status = "OK" if ok else "BAD"
    suffix = f" - {issues}" if issues else ""
    print(f"[image-qa] attempt {attempt} [{short}]: {status}{suffix}")


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
    _log_qa_result(attempt, model, ok, issues)
    if not ok:
        errors.append(f"{model}: {issues}")
    return img, ctype, ok


def _generate_image_with_qa(prompt: str, errors: list) -> tuple | None:
    """Try Cloudflare models with QA check. Returns ONLY a clean (OK) render.

    Never ships a BAD render with garbled baked-in text - a garbled background
    plus a PIL overlay is what produced the double-text mess. If every attempt
    has baked text, return None so the caller falls back to a clean placeholder
    + crisp PIL cards instead.
    """
    models = ("@cf/black-forest-labs/flux-2-klein-4b", "@cf/black-forest-labs/flux-2-dev",
              "@cf/black-forest-labs/flux-1-schnell", "@cf/stabilityai/stable-diffusion-xl-base-1.0")
    for attempt in range(1, IMAGE_QA_MAX_ATTEMPTS + 1):
        for model in models:
            result = _try_single_model_qa(model, prompt, attempt, errors)
            if result is None:
                continue  # network error, try next model
            img, ctype, is_ok = result
            if is_ok:
                return img, ctype
            # BAD = baked garbled text -> discard, never use as fallback.
            # Continue to next model (fresh background) instead of breaking.
    print(f"[image-qa] all {IMAGE_QA_MAX_ATTEMPTS} attempts had baked text - "
          f"discarding garbled renders, will use clean placeholder + overlay")
    return None


# Phrases that make diffusion models attempt (and garble) text. Stripped
# from the background prompt - ALL real text is drawn later via PIL.
_TEXT_TRIGGERS_RE = re.compile(
    r"(perfectly legible English|legible English|readable text|speech bubbles?|"
    r"text bubbles?|with text|caption labels?|word labels?|headers? with words?|"
    r"bullet lines?|quote text|attribution line|dated labels?|milestone nodes? with[^,]*|"
    r"exact word[^,]*|short labels?[^,]*|bold (headers?|titles?|labels?)[^,]*|"
    r"huge (headline|numbers?|quote)[^,]*|number labels?[^,]*),?",
    flags=re.IGNORECASE,
)

_NO_TEXT_SUFFIX = (
    "ABSOLUTELY NO TEXT, no letters, no words, no numbers, no signs, no captions, "
    "no speech bubbles, no labels anywhere. Pure abstract background illustration only: "
    "icons, shapes and empty blank cream panels with generous empty space at the top "
    "for a title banner and empty cards at the bottom for text overlay. Clean, "
    "uncluttered, blurred background details so overlaid text stays legible."
)


def _sanitize_elements(elements: dict) -> dict:
    """Clean + dedupe extracted texts so overlay stays legible and creative."""
    def _clean_one(s: str, max_words: int = 5, max_chars: int = 32) -> str:
        s = re.sub(r"\s+", " ", (s or "").strip().strip("\"'")).strip()
        s = s[:max_chars].strip()
        words = s.split()
        if len(words) > max_words:
            s = " ".join(words[:max_words])
        return s

    headline = _clean_one(elements.get("headline", ""), max_words=6, max_chars=42)
    seen: set[str] = set()
    labels: list[str] = []
    for raw in elements.get("labels", []) or []:
        lab = _clean_one(raw)
        if not lab or lab.lower() in seen:
            continue
        seen.add(lab.lower())
        labels.append(lab)
        if len(labels) >= 6:  # supports all 15 templates: 2x2 grid (3-4) up to 2x3 grid (5-6 for comic/billboard/checklist/roadmap)
            break
    return {"headline": headline, "labels": labels}


def _extract_text_and_clean_prompt(image_prompt: str) -> tuple[dict, str]:
    """Extract text elements for PIL overlay and return a STRICTLY text-free background prompt."""
    matches = _EXACT_TEXT_RE.findall(image_prompt)
    elements = _sanitize_elements({"headline": matches[0] if matches else "", "labels": matches[1:]})
    clean = _CLEAN_TEXT_RE.sub("", image_prompt)
    clean = _TEXT_TRIGGERS_RE.sub("", clean)
    clean = re.sub(r"\s{2,}", " ", clean).strip(" ,.")
    clean = f"{clean}\n\n{_NO_TEXT_SUFFIX}"
    return elements, clean.strip()


def _load_font(size: int):
    """Load a bold truetype font. Same file on Windows and ubuntu-latest runner."""
    candidates = [
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    """Wrap text into lines that fit max_width pixels."""
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        trial = f"{current} {word}".strip()
        bbox = draw.textbbox((0, 0), trial, font=font)
        if bbox[2] - bbox[0] <= max_width or not current:
            current = trial
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _fit_font(draw: ImageDraw.ImageDraw, text: str, start_size: int, max_width: int,
              min_size: int = 18) -> object:
    """Shrink font until the longest word fits max_width."""
    size = start_size
    while size > min_size:
        font = _load_font(size)
        words = text.split() or [""]
        widest = max(draw.textbbox((0, 0), wd, font=font)[2] for wd in words)
        if widest <= max_width:
            return font
        size -= 2
    return _load_font(min_size)


# Accent strip colors per card - corporate comparison feel, deterministic order.
# 6 entries so 6-item templates (comic/billboard/checklist/roadmap) each get a unique color.
_CARD_ACCENTS = [(20, 184, 166), (249, 115, 22), (59, 130, 246),
                 (34, 197, 94), (168, 85, 247), (236, 72, 153)]

_DARK_BANNER = (15, 23, 42, 255)
_CARD_FILL = (255, 251, 235, 255)
_CARD_SHADOW = (15, 23, 42, 255)
_HEADLINE_TEXT = (255, 255, 255, 255)
_LABEL_TEXT = (17, 24, 39, 255)


def _draw_headline_banner(img: Image.Image, draw: ImageDraw.ImageDraw,
                          w: int, h: int, headline: str) -> int:
    """Draw opaque top banner + wrapped headline. Returns banner bottom y.

    Opaque banner (not floating outline text) guarantees legibility on any
    diffusion background and covers any garbled baked text underneath.
    """
    text = headline.upper()
    margin = int(w * 0.05)
    max_text_w = w - 2 * margin - 40
    font = _fit_font(draw, text, max(30, int(w * 0.062)), max_text_w)
    lines = _wrap_text(draw, text, font, max_text_w)[:2]
    line_heights = [draw.textbbox((0, 0), ln, font=font)[3] for ln in lines]
    text_h = sum(line_heights) + (10 * (len(lines) - 1) if len(lines) > 1 else 0)
    banner_h = text_h + int(h * 0.055)
    banner_h = max(banner_h, int(h * 0.13))

    # Banner background + thin accent line at its base.
    draw.rounded_rectangle([(0, 0), (w, banner_h)], radius=0, fill=_DARK_BANNER)
    draw.rectangle([(0, banner_h - 6), (w, banner_h)], fill=(20, 184, 166, 255))

    y = (banner_h - text_h) // 2
    for ln in lines:
        bbox = draw.textbbox((0, 0), ln, font=font)
        x = (w - (bbox[2] - bbox[0])) // 2
        draw.text((x, y), ln, font=font, fill=_HEADLINE_TEXT,
                  stroke_width=1, stroke_fill=(0, 0, 0, 200))
        y += (bbox[3] - bbox[1]) + 10
    return banner_h


def _draw_label_grid(img: Image.Image, draw: ImageDraw.ImageDraw, w: int, h: int,
                     labels: list[str], top: int) -> None:
    """Draw labels as opaque cards in an aligned grid below `top`.

    Each card is drawn by us (box + text together), so text can never drift
    off its card - the misalignment in the old center-stack code is impossible.
    Layout adapts to every template style:
      1 card -> centered, 2 -> 1 row, 3-4 -> 2x2 grid,
      5-6 -> 2 cols x 3 rows (comic / billboard / checklist / roadmap / timeline).
    """
    n = len(labels)
    if n == 0:
        return
    gap = int(w * 0.035)
    side = int(w * 0.06)

    if n == 1:
        boxes = [(side, None, w - side, None)]
    elif n == 2:
        card_w = (w - 2 * side - gap) // 2
        boxes = [(side, None, side + card_w, None),
                 (side + card_w + gap, None, w - side, None)]
    else:
        import math
        rows_needed = math.ceil(n / 2)
        card_w = (w - 2 * side - gap) // 2
        boxes = []
        for r in range(rows_needed):
            boxes.append((side, r, side + card_w, r))
            if len(boxes) < n:
                boxes.append((side + card_w + gap, r, w - side, r))
        rows = rows_needed
    if n <= 2:
        rows = 1
    avail_h = h - top - int(h * 0.04)
    card_h = min(int(h * 0.20), (avail_h - gap * (rows - 1)) // rows)
    # Vertically center the whole grid in the remaining space.
    grid_h = rows * card_h + (rows - 1) * gap
    start_y = top + max(int(h * 0.03), (avail_h - grid_h) // 2)

    # Smaller text when 5-6 cards share the space (billboard/checklist/roadmap styles).
    base_font_size = max(20, int(w * 0.032)) if n <= 4 else max(16, int(w * 0.026))
    for idx, (label, box) in enumerate(zip(labels, boxes)):
        x0, _, x1, _ = box
        row = box[1] if n > 2 else 0
        y0 = start_y + row * (card_h + gap)
        y1 = y0 + card_h

        # Solid offset shadow (neo-brutalist) then card.
        # Solid fill (not alpha) because RGBA->RGB conversion turns
        # semi-transparent black into harsh pure-black edges.
        draw.rounded_rectangle([(x0 + 4, y0 + 4), (x1 + 4, y1 + 4)],
                               radius=26, fill=_CARD_SHADOW)
        draw.rounded_rectangle([(x0, y0), (x1, y1)], radius=26, fill=_CARD_FILL,
                               outline=_CARD_SHADOW, width=3)
        # Accent strip inset inside the card so corners never poke out.
        accent = _CARD_ACCENTS[idx % len(_CARD_ACCENTS)]
        draw.rectangle([(x0 + 2, y0 + 2), (x1 - 2, y0 + 12)], fill=accent + (255,))

        max_text_w = (x1 - x0) - 36
        font = _fit_font(draw, label, base_font_size, max_text_w, min_size=16)
        lines = _wrap_text(draw, label.upper(), font, max_text_w)[:2]
        lh = [draw.textbbox((0, 0), ln, font=font)[3] for ln in lines]
        block_h = sum(lh) + 8 * (len(lines) - 1)
        ty = y0 + (card_h - block_h) // 2 + 6  # +6 to clear accent strip
        for ln in lines:
            bbox = draw.textbbox((0, 0), ln, font=font)
            tx = x0 + ((x1 - x0) - (bbox[2] - bbox[0])) // 2
            draw.text((tx, ty), ln, font=font, fill=_LABEL_TEXT)
            ty += (bbox[3] - bbox[1]) + 8


def _overlay_text(image_bytes: bytes, elements: dict) -> bytes:
    """Overlay headline banner + label cards onto image using PIL.

    Keeps your creative text, but draws box+text together in fixed grid
    positions instead of floating outline text at image center.
    """
    elements = _sanitize_elements(elements)
    img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    draw = ImageDraw.Draw(img)
    w, h = img.size
    top = 0
    if elements["headline"]:
        top = _draw_headline_banner(img, draw, w, h, elements["headline"])
    if elements["labels"]:
        _draw_label_grid(img, draw, w, h, elements["labels"], top)
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
        return image_bytes, content_type, "png"  # force ext to match bytes (was "jpg")
    new_bytes, new_ctype = _normalize_to_png(image_bytes, content_type)
    return new_bytes, new_ctype, "png"


def _create_placeholder_image() -> bytes:
    """Create a clean textured 1024x1024 background when diffusion fails.

    Deliberately text-free with empty top/bottom space - the PIL banner +
    cards drawn on top still make it look like a finished infographic.
    """
    size = 1024
    top_color = (240, 253, 250)
    bottom_color = (255, 251, 235)
    img = Image.new("RGB", (size, size), color=top_color)
    draw = ImageDraw.Draw(img)
    for y in range(size):
        t = y / size
        r = int(top_color[0] + (bottom_color[0] - top_color[0]) * t)
        g = int(top_color[1] + (bottom_color[1] - top_color[1]) * t)
        b = int(top_color[2] + (bottom_color[2] - top_color[2]) * t)
        draw.line([(0, y), (size, y)], fill=(r, g, b))
    # faint circuit-like grid for tech feel, kept low-contrast so text pops
    for i in range(0, size, 64):
        draw.line([(i, 0), (i, size)], fill=(203, 213, 225, 255), width=1)
        draw.line([(0, i), (size, i)], fill=(203, 213, 225, 255), width=1)
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
    if not elements["headline"] and not elements["labels"]:
        # Last resort: never ship 0-overlay (pure diffusion text). Derive from post.
        elements = _fallback_elements(state.get("post_text", ""), state.get("topic", ""))
        print(f"[image] Gemini gave 0 text fragments, fallback overlay: "
              f"\"{elements['headline']}\" + {len(elements['labels'])} labels")
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