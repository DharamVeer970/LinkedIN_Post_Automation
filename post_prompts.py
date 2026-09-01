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

ALL_DOMAIN_NAMES = list(TOPIC_DOMAINS.keys())
ALL_RSS_FEEDS = [f for d in TOPIC_DOMAINS.values() for f in d["feeds"]]
ALL_EVERGREEN_TOPICS = [t for d in TOPIC_DOMAINS.values() for t in d["evergreen"]]


# ====================================================================
# 2) IMAGE STYLE ENGINE - AGENT-DRIVEN (exactly like your 2 references)
#    Gemini IS the art director. It picks ONE of the 2 viral templates
#    and writes the complete ready-to-use prompt. Pipeline just uses it
#    as-is + seed. No random style layering.
# ====================================================================

# 5 proven viral templates - reverse-engineered from your examples
VIRAL_TEMPLATES = {
    "comic": (
        "6-panel comic strip, 3 rows x 2 columns, thick black panel borders, "
        "flat vector cartoon style, clean bold outlines, soft flat colors, "
        "speech bubbles with SHORT perfectly legible English text (max 8 words per bubble), "
        "expressive characters, white background, trending LinkedIn humor comic"
    ),
    "roadmap": (
        "dark navy tech roadmap infographic, vertical glowing flowchart with luminous "
        "cyan-teal connections branching from left central node into 7 glassmorphism "
        "category cards on the right, each card with small icon and 2-3 word label, "
        "circuit-board background with faint traces, neon glow, premium dark vector, "
        "perfectly legible English labels, no gibberish"
    ),
    "comparison": (
        "premium corporate comparison infographic, 4 large rounded cards in 2x2 grid "
        "each with bold color-coded header, icon and 3 bullet lines, top hero with "
        "isometric 3D illustrations on circuit board, cream-beige background with subtle "
        "grid and technical line art, crisp vector editorial layout, perfectly legible English"
    ),
    "sketch_story": (
        "editorial hand-drawn explainer infographic, white background with ink sketch "
        "illustrations, central bridge/arch metaphor connecting two concepts, speech bubbles, "
        "arrows, small character silhouettes, orange and blue accent highlights, newspaper "
        "infographic style, clean doodle with perfectly legible English typography"
    ),
    "billboard": (
        "bold minimal poster infographic, huge black condensed headline at top occupying "
        "30% of canvas, 6 uniform hand-sketched icons in 2x3 grid below each with short "
        "bold label underneath, off-white paper background, high contrast, viral billboard style, "
        "perfectly legible English"
    ),
    "animated-image": (
        "animated image, 6 panels in 2x3 grid, each panel with smooth transitions, "
        "flat vector cartoon style, clean bold outlines, soft flat colors, "
        "speech bubbles with SHORT perfectly legible English text (max 8 words per bubble), "
        "expressive characters, with beautiful background, trending LinkedIn humor comic, "
        "crisp vector, 8k, ultra-detailed, perfectly legible English, "
        "showing a smooth flow of the story in each panel, with a clear beginning, middle, and end, "
    ),

    "automation-flow-diagram": (
        "automation flow diagram, matching background with lines, don't overcrowd, clear and simple, "
        "with a clear beginning, middle, and end, "
        "with clearly moving cutting-edge lines connecting each process to the next, "
    ),
}

# For pipeline compatibility and manual override - not used when agent-driven
IMAGE_STYLES = list(VIRAL_TEMPLATES.values())
INFOGRAPHIC_FORMATS = IMAGE_STYLES
COLOR_THEMES = ["default palette - style already includes colors"]
NEGATIVE_PROMPT = (
    "blurry, low resolution, pixelated, distorted, watermark, logo, "
    "gibberish text, misspelled, lorem ipsum, extra fingers, photorealistic face"
)


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


def image_prompt_gen(post_text: str) -> str:
    return (
        "You are a viral LinkedIn art director. Pick EXACTLY ONE template and write "
        "ONE ready-to-use image prompt (60-80 words, comma phrases).\n\n"
        "TEMPLATE A - COMIC (humor/relatable/AI fails):\n"
        f"\"{VIRAL_TEMPLATES['comic']}\" + describe 6 panels + EXACT speech bubble texts (5-8 words each, funny, FROM the post)\n\n"
        "TEMPLATE B - ROADMAP (tech stacks/tools/skills, e.g. AI Agent Development):\n"
        f"\"{VIRAL_TEMPLATES['roadmap']}\" + list 6-7 categories and 3-4 items per category with icons, ALL TEXT FROM post\n\n"
        "TEMPLATE C - COMPARISON (comparisons like GPU vs TPU, X vs Y):\n"
        f"\"{VIRAL_TEMPLATES['comparison']}\" + 4 cards with headers, bullets and hero illustration, ALL TEXT FROM post\n\n"
        "TEMPLATE D - SKETCH_STORY (paradox/narrative/future-of-work):\n"
        f"\"{VIRAL_TEMPLATES['sketch_story']}\" + bridge metaphor, annotations, 3 levels, ALL TEXT FROM post\n\n"
        "TEMPLATE E - BILLBOARD (listicles like 'How to stay poor', 5-6 habits):\n"
        f"\"{VIRAL_TEMPLATES['billboard']}\" + huge headline + 6 icons with labels, ALL TEXT FROM post\n\n"
        "CRITICAL CAPTION ALIGNMENT RULE (must follow):\n"
        "- Every word inside the image (bubbles/labels/headers/bullets) MUST be directly "
        "taken from or paraphrased from the Post below. Do NOT invent unrelated labels. "
        "If post is about 'RAG mistakes', image must say RAG-related labels, not generic AI.\n"
        "RULES:\n"
        "- Choose the ONE template that best fits the post's structure.\n"
        "- Output ONLY the final prompt, no explanation.\n"
        "- Keep each text fragment under 5 words for legibility.\n"
        "- Use AT MOST 8 separate text fragments in the whole image. Fewer is better - "
        "text models misspell long text. Prefer icons + 1-3 word labels over sentences.\n"
        "- Write every text fragment inside double quotes, e.g. label \"AI Agents\", and "
        "prefix the quote with: exact text. The image model must render each quoted "
        "string letter-for-letter with CORRECT spelling, no invented words, no gibberish.\n"
        "- Keep labels grammatically correct and natural in plain English "
        "(e.g., short noun phrases or simple verb phrases).\n"
        "- Avoid rare/technical spellings, acronyms longer than 5 letters, and punctuation "
        "inside quoted labels (apostrophes, ampersands) - they cause typos.\n"
        "- Always end with: 'crisp vector, 8k, ultra-detailed, perfectly legible English, "
        "all text spelled exactly as quoted, grammatically correct'\n\n"
        f"Post:\n{post_text[:500]}"
    )


def image_qa_prompt() -> str:
    return (
        "Look at this image. List ALL visible text exactly as written, then check:\n"
        "1. Is every word a correctly-spelled English word?\n"
        "2. Is every text fragment readable (not garbled/distorted letters)?\n"
        "3. Is each text fragment grammatically correct or naturally phrased English?\n"
        "4. Do the texts make sense together (no random gibberish strings)?\n"
        "Answer in EXACTLY this format, nothing else:\n"
        "VERDICT: OK   (if every visible word is spelled correctly, readable, and grammatically natural)\n"
        "VERDICT: BAD  (if ANY word is misspelled, garbled, grammatically wrong, or gibberish)\n"
        "ISSUES: <comma-separated list of each wrong/garbled text you see; "
        "write 'none' if verdict is OK>"
    )


def highlights_prompt(post_text: str) -> str:
    return (
        "From this LinkedIn post, extract a short title (3-6 words, no hashtags, no emoji) "
        "and exactly 3 crisp highlight phrases (each under 5 words) that summarize the "
        "flow/key idea. These will be rendered as text inside an infographic image. "
        "Return in this EXACT format, nothing else:\n"
        "TITLE: <title>\n"
        "1. <highlight 1>\n"
        "2. <highlight 2>\n"
        "3. <highlight 3>\n\n"
        f"Post:\n{post_text}"
    )
