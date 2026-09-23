"""
post_prompts.py - single source of truth for ALL text prompts, topic sources,
and image-style presets used by linkedin_pipeline.py.

Edit THIS file to change how posts sound, what topics are covered, and what
the generated artwork looks like - no need to touch the pipeline code.
"""

# ====================================================================
# 1) TOPIC DOMAINS - broader than just AI.
#    Each domain has free RSS feeds (live topics) + evergreen topics.
# ====================================================================
TOPIC_DOMAINS = {
    "AI & Technology": {
        "feeds": [
            "https://export.arxiv.org/rss/cs.AI",
            "https://export.arxiv.org/rss/cs.CL",
            "https://www.artificialintelligence-news.com/feed/",
        ],
        "evergreen": [
            "Agentic AI and multi-agent systems",
            "How AI agents autonomously complete multi-step workflows",
            "Building reliable AI agents that don't break in production",
            "The rise of small language models (SLMs) over bloated giants",
            "Fine-tuning vs RAG: when to use which",
            "Evaluating LLM applications in production",
            "The hidden cost of LLM API latency and how to cut it",
            "Re-ranking in RAG: why retrieval quality matters most",
            "Model distillation: training smaller models from big ones",
            "Why context engineering beats cleverer prompting",
            "AI code assistants: where they shine and where they lie",
            "Open-source models vs closed APIs: real pricing math",
            "Structured outputs from LLMs: JSON mode done right",
            "AI infrastructure economics: GPUs, inference, and cost realities",
            "The future of AI agents: from tools to autonomous systems",
            "Multi-modal agents: AI that can see, hear, and act",
            "Tool use: how LLMs call external tools and APIs",
            "Multi-agent orchestration: coordinating agents for complex tasks",
            "The ethics of AI agents: responsibility, bias, and safety",
        ],
    },
    "Software & Automation": {
        "feeds": [],
        "evergreen": [
            "Why automation is the highest-leverage skill in software",
            "Turning manual tasks into Python scripts (practical automation)",
            "Workflow automation: connecting tools without writing glue code",
            "Agentic automation: letting AI drive your repetitive workflows",
            "How to build a personal knowledge base in automation age",
            "When NOT to automate: the hidden cost of over-engineering",
            "Robotic process automation vs AI agents vs traditional scripts",
            "Scheduling jobs that actually run: cron, queues, and retries",
            "Building backend services that never go down",
            "API design: versioning, rate limits, and backward compatibility",
            "Command-line tools: the fastest way to work in software",
            "Git workflows that save your team hours every week",
            "From dev to deploy: automating your entire release pipeline",
            "Open source vs SaaS for developers: honest trade-offs",
            "Automation in the cloud: serverless, containers, and orchestration",
            "Measuring automation ROI: impact vs. maintenance costs",
            "Self-healing systems: when software fixes itself in production",
            "Unit testing vs integration testing: what to automate and when",
            "Debugging strategies for complex automated systems",
        ],
    },

    "Latest DevNews & Models": {
        "feeds": [
            "https://export.arxiv.org/rss/cs.AI",
            "https://export.arxiv.org/rss/cs.CL",
            "https://export.arxiv.org/rss/cs.SE",
            "https://hnrss.org/frontpage",
            "https://techcrunch.com/feed/",
        ],
        "evergreen": [
            "The latest state-of-the-art language model releases",
            "New model benchmarks: what metrics actually matter",
            "Transformer alternatives: the future of language modeling",
            "Open-weight vs closed models: the 2026 landscape",
            "How newer models are changing agent workflows",
            "Local LLMs: running powerful models on consumer hardware",
            "The new batch of coding agents and AI engineers",
            "Production LLM monitoring: tokens, costs, and drift",
            "Vector databases: which one fits your stack",
            "New inference engines: how models get faster without losing quality",
            "The rise of multi-modal models: text, image, and beyond",
            "Model compression and quantization: making giants run on laptops",
        ],
    },
    "Startups & Business": {
        "feeds": [
            "https://techcrunch.com/feed/",
            "https://www.entrepreneur.com/feed",
        ],
        "evergreen": [
            "Why most startups fail at distribution, not product",
            "Bootstrapping vs venture capital: honest trade-offs",
            "Building a personal brand as a founder",
            "Pricing psychology every founder should know",
            "How solo founders ship faster than big teams",
            "Customer interviews: asking questions that reveal truth",
            "The lean startup method: build, measure, learn",
        ],
    },
    "Productivity & Career": {
        "feeds": [
            "https://hnrss.org/frontpage",
        ],
        "evergreen": [
            "Deep work: why focus is the new superpower",
            "Career advice nobody tells early engineers",
            "How to learn hard things twice as fast",
            "Managing energy, not time: a practical system",
            "Saying no: the highest-leverage skill at work",
            "From individual contributor to leader: real lessons",
            "The 80/20 rule in career growth: what to focus on",
        ],
    },
    "Psychology & Mind": {
        "feeds": [],
        "evergreen": [
            "The psychology of habits: why willpower fails",
            "Imposter syndrome: what it really is and isn't",
            "Why smart people make bad decisions under stress",
            "The dopamine trap: phones, feeds and focus",
            "Cognitive biases that shape your daily choices",
            "How curiosity rewires the brain for learning",
            "The science of motivation: intrinsic vs extrinsic",
            "Mindfulness and productivity: separating hype from science",
        ],
    },
    "Science & Future": {
        "feeds": [
            "https://www.sciencedaily.com/rss/all.xml",
        ],
        "evergreen": [
            "Fusion energy: how close are we really?",
            "What brain-computer interfaces mean for humans",
            "Space manufacturing: the next industrial revolution",
            "Longevity science: slowing biological aging",
            "Quantum computing myths vs reality",
            "Synthetic biology: programming living cells",
            "The future of human-machine symbiosis",
            "The ethics of AI: navigating the moral landscape",
        ],
    },
    "Design & Creativity": {
        "feeds": [
            "https://www.smashingmagazine.com/feed/",
        ],
        "evergreen": [
            "Design thinking is dead; here's what replaces it",
            "Why great products feel obvious in hindsight",
            "Typography tricks that instantly upgrade any UI",
            "Creativity is a process, not a lightning strike",
            "Minimalism in design: less, but better",
            "How constraints make designers more creative",
            "The tool-agnostic designer: why process beats software",
            "Color theory for non-designers: practical applications",
        ],
    },
    "Money & Investing": {
        "feeds": [],
        "evergreen": [
            "Compound interest: the math nobody feels until it's late",
            "Index funds vs stock picking: an honest comparison",
            "Lifestyle inflation: the silent wealth killer",
            "Skills that pay forever in any economy",
            "The psychology of spending: why we buy",
            "Side income myths vs what actually works",
            "The FIRE movement: financial independence, retire early",
            "In this ERA of AI, what skills will retain value?",
        ],
    },
}

ALL_RSS_FEEDS = [f for d in TOPIC_DOMAINS.values() for f in d["feeds"]]
ALL_EVERGREEN_TOPICS = [t for d in TOPIC_DOMAINS.values() for t in d["evergreen"]]


CONCEPT_VISUAL_EXAMPLES = """
- Parallel/divided work (e.g. multiple specialized agents each owning a task):
  several distinct real workers or objects positioned side by side in one
  frame, each doing a visibly different action, unified by shared lighting -
  NOT a single path or sequence.
- Sequential/step-by-step (e.g. a journey, a growing process over time):
  one continuous path or progression the eye follows in order, like a trail
  with waypoints, or the same subject shown across successive stages.
- Comparison/contrast (e.g. X vs Y, old way vs new way):
  a clean visual split between two real scenes or objects that embody each
  side, unified by matching composition and lighting.
- Hierarchy/framework (e.g. levels of a skill, a pyramid of needs):
  physical objects stacked or layered by size/height, biggest or foundational
  at the base.
- Overlap/intersection (e.g. where two ideas or skill sets meet):
  two or three translucent forms (light, glass, fabric) physically overlapping,
  the overlap itself visually distinct.
- Single powerful insight/quote: one quiet, evocative real moment or object
  that embodies the idea - no need for multiple elements at all.
"""


VISUAL_DIRECTIONS = [
    "6-panel comic strip with expressive characters and thick black panel borders",
    "4-panel mistake-to-fix story with warning icons, anxious face, then resolution",
    "two-character dialogue scene with short speech bubbles and a clear visual punchline",
    "hand-drawn bridge metaphor connecting old workflow to better outcome",
    "before-and-after split sketch showing broken process versus safer process",
    "whiteboard doodle map with arrows, simple objects, and one central metaphor",
    "ladder of levels where each step shows a practical maturity stage",
    "cause-effect chain with objects triggering the next visible consequence",
    "warning-to-solution scene with hazard signs, guardrails, and a final safe path",
    "mini roadmap with 3 checkpoints drawn as physical gates or stations",
    "workbench scene where people compare modular pieces and choose the safer one",
    "factory line metaphor where bad inputs are inspected before deployment",
    "detective investigation board with clues, pins, and a clear root-cause reveal",
    "mentor-student whiteboard lesson with one confused learner and one clear fix",
    "control-room scene with dashboards replaced by physical gauges and human review",
]


# ====================================================================
# 3) TEXT PROMPTS - each function returns the full prompt string.
# ====================================================================

def content_prompt(topic: str) -> str:
    return (
        f"Write a scroll-stopping LinkedIn post about: '{topic}'.\n"
        "Style rules:\n"
        "- Open with a punchy one-line hook (bold claim, surprising fact, or provocative question) "
        "ending with a fitting emoji.\n"
        "- Then 3-4 short, punchy paragraphs (1-2 sentences each). No fluff.\n"
        "- Use emojis EXPRESSIVELY: 5-7 total, placed where they add feeling or emphasis - "
        "e.g. ⚡ for speed/energy, 💡 for insights, 🎯 for precision/takeaway, 🔥 for hype, "
        "💰 for money, 🧠 for thinking, 📉📈 for trends, ❌✅ for do/don't contrasts. "
        "At least one emoji per paragraph where it needed, but never two in a row and never mid-word.\n"
        "- Weave 2-3 relevant hashtags naturally INSIDE the body sentences "
        "(e.g. '...thanks to #AgenticAI ...'), not all dumped at the end.\n"
        "- Include exactly ONE concrete takeaway or actionable insight.\n"
        "- Close with a strong one-liner + emoji, then a final line of 5-7 additional hashtags "
        "(e.g. #AI #Innovation #Growth). This final hashtag line is mandatory.\n"
        "- Before answering, silently self-check every rule above (hook under 200 characters, "
        "5-7 emojis, hashtags woven into the body, mandatory final hashtag line, exactly one "
        "concrete takeaway, and NO cliches like 'game-changer', 'in today's world' or "
        "'delve into') and fix anything that fails. Getting it right first time avoids a "
        "whole extra review round.\n"
        "Return only the post text, nothing else."
    )


def critique_prompt(post_text: str) -> str:
    return (
        "You are a strict, experienced LinkedIn content critic for a broad professional audience. "
        "Evaluate this draft on:\n"
        "1. HOOK: is the first line under ~200 characters and strong enough to stop the scroll?\n"
        "2. CLICHES: does it use tired phrases like 'game-changer', 'in today's world', 'delve into'?\n"
        "3. VALUE: one concrete, specific takeaway (not generic advice)?\n"
        "4. ENGAGEMENT: does it end with a question or call-to-action inviting comments?\n"
        "5. LENGTH: is it concise enough to be read fully on mobile?\n"
        "6. EMOJI USE: 5-7 expressive emojis placed where they add feeling (one per paragraph)? "
        "Too few feels flat; too many looks spammy.\n"
        "Score 1-10 overall. Be harsh - typical AI-generated posts should score 5-6.\n"
        "Return EXACTLY in this format, nothing else:\n"
        "SCORE: <number>\n"
        "FEEDBACK: <the single most impactful improvement, one sentence>\n\n"
        f"Post:\n{post_text}"
    )


def revise_prompt(post_text: str, feedback: str) -> str:
    return (
        f"Improve this LinkedIn post based on this critic feedback: '{feedback}'.\n"
        "Strict rules while improving:\n"
        "- Keep the same topic and core message.\n"
        "- First line MUST stay under 200 characters and work as a scroll-stopping hook.\n"
        "- No cliches like 'game-changer', 'in today's world', 'delve into'.\n"
        "- Use 5-7 well-placed emojis that add feeling or emphasis (at least one per paragraph, "
        "never two in a row).\n"
        "- Keep hashtags woven naturally inside the body.\n"
        "- Keep the mandatory final hashtag line as the very last line.\n"
        "- End with a short question or call-to-action inviting comments.\n"
        "Return only the improved post text, nothing else.\n\n"
        f"Original post:\n{post_text}"
    )


def image_prompt_gen(post_text: str, topic: str = "", visual_direction: str = "") -> str:
    return (
        "You are a viral LinkedIn cartoon-infographic art director. Create an image like "
        "a hand-drawn LinkedIn explainer: comic panels, speech bubbles, sketch labels, "
        "bridge metaphors, arrows, and simple characters/objects that tell the idea.\n"
        f"POST TOPIC LABEL: {topic or 'unspecified'}\n\n"
        f"THIS RUN'S VISUAL DIRECTION: {visual_direction or 'choose the best fitting direction'}.\n"
        "Follow this direction unless it clearly conflicts with the post.\n\n"
        "Silently identify the post's structure: parallel work, sequence, comparison, "
        "hierarchy, overlap/intersection, or one standalone insight. Then choose ONE "
        "visual format: 6-panel comic story, 4-panel mistake-to-fix story, two-character "
        "dialogue, hand-drawn bridge explainer, before/after split sketch, whiteboard "
        "doodle map, ladder of levels, cause-effect chain, warning-to-solution scene, "
        "mini roadmap, or simple process flow. Inspiration only:\n"
        f"{CONCEPT_VISUAL_EXAMPLES}\n"
        "Return only a 90-130 word image prompt. Start with the concrete subject from "
        "the topic/post. Include 2-4 EXACT readable text fragments in quotes, each 1-3 "
        "simple English words, and make them part of the illustration as speech bubbles, "
        "callout labels, or sign text. Use no other text.\n\n"
        "Rules: clean hand-drawn cartoon infographic style, thick black outlines, white "
        "background, LinkedIn explainer look, topic-specific artifacts, expressive human "
        "or robot characters when useful, clear flow/story, generous spacing, large lettering, "
        "no photo-realistic stock image. "
        "For AI/model topics, show concrete model ecosystem artifacts: server racks, GPU "
        "trays, open workbenches, modular model blocks, researchers comparing systems, "
        "deployment paths. Avoid loose symbols like crowns, chess pieces, trophies, random "
        "monoliths, generic glowing brains, circuit wallpaper, and robot hands. Every quoted "
        "text fragment must be spelled exactly and legible; no gibberish, no extra letters, "
        "no repeated filler text, no watermark.\n\n"
        f"Post:\n{post_text[:500]}"
    )


def image_qa_prompt(post_context: str = "") -> str:
    relevance = ""
    if post_context.strip():
        relevance = (
            "RELEVANCE CHECK (caption-image match): this image was generated for a LinkedIn "
            "post with this context:\n"
            f"\"{post_context[:500]}\"\n"
            "Would a reader of that post instantly recognize the picture as being about the "
            "same idea? PASS when the scene clearly shows the post's central subject with "
            "concrete domain artifacts. Creative metaphors count only when anchored by the "
            "topic's real domain. FAIL when the image is generic stock filler, abstract "
            "wallpaper, or only a loose symbol like crowns, chess pieces, scales, trophies, "
            "random monoliths, or power objects with no visible connection to the topic.\n\n"
        )
    return (
        "You are a text-proofreader AND caption-relevance reviewer for an AI-generated image. "
        "Diffusion models often garble text AND drift off-topic, so verify both before "
        "passing it.\n\n"
        "Expected style: a hand-drawn LinkedIn explainer, comic, bridge sketch, comparison "
        "sketch, or simple process flow. Speech bubbles and short labels are allowed only "
        "when they are readable, correctly spelled, grammatical, and relevant. FAIL generic "
        "photo-realistic stock images, random decorative overlays, slide/dashboard UI, or "
        "abstract symbols with no concrete connection to the topic. A fully text-free image "
        "is OK when it is clearly related to the post's topic.\n\n"
    ) + relevance + (
        "STEP 1 - TRANSCRIBE: List every separate text fragment that is meant to "
        "be read - titles, labels, bullets, signs, anything inside speech bubbles. "
        "Ignore text that is too small, blurry, or in the far background to read "
        "confidently - do not count it as a fragment. Count only what you list.\n"
        "STEP 2 - CHECK each listed fragment against ALL rules:\n"
        "a. Spelling: every word must be a correctly-spelled English word "
        "(or a well-known proper noun/acronym like AWS, GPU). Reject any invented, "
        "misspelled, or half-real word (e.g. 'CLUTEP', 'PRODUCTIVIY').\n"
        "b. Legibility: no distorted, melted, overlapping, or doubled letters in "
        "fragments meant to be read clearly.\n"
        "c. Grammar: each fragment must be natural, grammatically valid English.\n"
        "d. Duplicates: the same label should not repeat as filler text.\n"
        "e. Gibberish: no random character strings or pseudo-text in any language.\n"
        "If a fragment you chose to list is genuinely unreadable even on a close "
        "look, mark it WRONG - do not guess.\n\n"
        "Answer in EXACTLY this format, nothing else:\n"
        "TEXTS: <all listed fragments, comma-separated, or 'none' if none qualify>\n"
        "VERDICT: <OK or BAD>\n"
        "ISSUES: <comma-separated list: the wrong text, layout problem, or 'UNRELATED: <the "
        "concrete scene this post actually needs instead>'; write 'none' if verdict is OK>"
    )
