"""
LinkedIn Daily Post Automation Pipeline (LangGraph)
Flow: topic (latest fetch + fallback) -> content -> self-critique/revision loop ->
      image prompt -> uniqueness check -> image gen (Stability AI ultra) -> post -> save history

Text generation: Google Gemini (latest Flash model)
Image generation: Stability AI ultra (api.stability.ai/v2beta/stable-image/generate/ultra)
Uniqueness tracking: local ChromaDB (similarity check against old posts)
Topics: multi-domain (AI, business, career, psychology, science, design, money)
        - free RSS feeds per domain + evergreen lists, defined in post_prompts.py
Prompts & image styles: all centralized in post_prompts.py
Credentials: loaded from a .env file (python-dotenv)
"""

import os
import json
import time
import random
import textwrap
import requests
import feedparser
import chromadb
from typing import TypedDict
from dotenv import load_dotenv
from langgraph.graph import StateGraph, END

# All text prompts, topic domains and infographic style presets live in post_prompts.py
from post_prompts import (
    TOPIC_DOMAINS,
    ALL_RSS_FEEDS,
    ALL_EVERGREEN_TOPICS,
    IMAGE_STYLES,
    INFOGRAPHIC_FORMATS,  # backward compat
    COLOR_THEMES,
    NEGATIVE_PROMPT,
    content_prompt,
    critique_prompt,
    revise_prompt,
    image_prompt_gen,
)

# linkedin_poster.py must exist in the same folder; posting functions are imported from there
from linkedin_poster import get_person_urn, register_image_upload, upload_image_binary, create_post

# ---- CONFIG: loaded from the .env file (create .env in this folder) ----
load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
LINKEDIN_TOKEN = os.getenv("LINKEDIN_TOKEN")
STABILITY_API_KEY = os.getenv("STABILITY_AI")

if not STABILITY_API_KEY:
    print("[warn] STABILITY_API_KEY not found in .env - image generation will fail. Add STABILITY_API_KEY=sk-...")
# Gemini 3.6 Flash: latest stable Flash model (gemini-2.0-flash is shut down per
# Google's deprecation list; gemini-2.5-flash was rate-limited for new keys).
GEMINI_MODEL = "gemini-3.6-flash"
SIMILARITY_THRESHOLD = 0.85                    # anything above this similarity is treated as duplicate
MAX_RETRIES = 3                                # how many times to retry with a new topic if duplicate found
MAX_REVISIONS = 2                              # how many times the critic loop may revise a weak draft
QUALITY_GATE = 7                               # post must score at least this to skip revision

# ---- Gemini API call settings (free-tier rate-limit protection) ----
API_MAX_RETRIES = 5                            # how many times to retry on 429/5xx/network errors
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
        collection.add(document=doc, id=f"post_{existing + i + 1}")
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


def call_gemini(prompt: str) -> str:
    """Call Gemini for text generation (free tier).

    Includes exponential-backoff retries for HTTP 429 (rate-limit) and
    5xx / network errors, and PRINTS the actual response body on a 429 so
    you can see exactly which quota was hit (RPM vs RPD vs TPM) instead of
    guessing. Honors the ``Retry-After`` header when present. A small
    pacing delay between successful calls keeps us under the free-tier
    requests-per-minute ceiling.

    Note: if the body says a per-DAY quota was exceeded, retrying within
    this run will NOT help - that quota only resets at midnight Pacific
    Time. In that case switch GEMINI_MODEL to a lite variant or wait.
    """
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    )
    body = {"contents": [{"parts": [{"text": prompt}]}]}

    last_exc = None
    for attempt in range(1, API_MAX_RETRIES + 1):
        try:
            resp = requests.post(url, json=body, timeout=60)
            status = resp.status_code

            # --- Rate-limited (429) or transient server errors (5xx) -> retry ---
            if status == 429 or status >= 500:
                print(f"[gemini] HTTP {status} body: {resp.text[:300]}")  # shows RPM vs RPD vs TPM
                wait = _gemini_backoff_wait(resp, attempt)
                print(f"[gemini] backing off {wait:.1f}s (attempt {attempt}/{API_MAX_RETRIES})")
                time.sleep(wait)
                continue

            # --- Non-retryable client errors (400, 401, 403, etc.) -> fail fast ---
            resp.raise_for_status()
            result = resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()

            # Pace subsequent calls to stay within the free-tier RPM limit
            time.sleep(API_PACE_DELAY)
            return result

        except (requests.exceptions.ConnectionError,
                requests.exceptions.Timeout) as exc:
            last_exc = exc
            wait = _gemini_backoff_wait(None, attempt)
            print(f"[gemini] network/timeout error ({type(exc).__name__}), "
                  f"backing off {wait:.1f}s "
                  f"(attempt {attempt}/{API_MAX_RETRIES})")
            time.sleep(wait)

    raise RuntimeError(
        f"Gemini API failed after {API_MAX_RETRIES} attempts - see the "
        "printed response bodies above for the exact quota that was hit"
    ) from last_exc


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
    topic = random.choice(pool)
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


# ---- Node 3: Generate image prompt (agent-driven, 5 templates) ----
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


# ---- Node 6: Generate the image - Stability AI Ultra (exactly your snippet)----
def generate_image(state: PipelineState) -> PipelineState:
    if not STABILITY_API_KEY:
        raise ValueError("STABILITY_API_KEY missing in .env - add STABILITY_API_KEY=sk-...")

    full_prompt = state["image_prompt"].strip()
    print(f"[image] Stability ultra | prompt: {full_prompt[:200]}...")

    response = requests.post(
        f"https://api.stability.ai/v2beta/stable-image/generate/ultra",
        headers={
            "authorization": f"Bearer {STABILITY_API_KEY}",
            "accept": "image/*"
        },
        files={"none": ''},
        data={
            "prompt": full_prompt,
            "output_format": "webp",
        },
    )

    if response.status_code == 200:
        image_path = os.path.join(IMAGES_DIR, f"post_image_{int(time.time())}.webp")
        with open(image_path, 'wb') as file:
            file.write(response.content)
        print(f"[image] saved {len(response.content)//1024}KB -> {image_path}")
        state["image_path"] = image_path
        return state
    else:
        try:
            err = response.json()
        except Exception:
            err = response.text[:500]
        print(f"[image] Stability error {response.status_code}: {err}")
        raise Exception(str(err))


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