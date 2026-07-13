"""Deterministic evidence compression + comparison synthesis (Phase 44.2).

The deterministic/offline final-answer provider (``AGENT_USE_REAL_LLM=false``)
previously dumped whole retrieved chunks into a multi-document comparison, with
duplicated bullets, biographical noise, and lexical shared/unique *token* lists.
This module replaces that with a compact, grounded, category-based synthesis:

- a maintainable **category → keyword taxonomy** (not a flat hardcoded list) is
  matched over the retrieved chunk text to extract concise technical skills;
- evidence is normalized (whitespace, wrapped lines) and de-duplicated;
- contact / education / extracurricular / leadership-only / header noise is
  excluded;
- similarities and differences are computed over **normalized technical concepts
  and categories**, not shared words;
- citations are **filename + page** only (never an opaque ``E#`` id);
- output is bounded and never a raw chunk dump.

Pure and config-free: no LLM, no clock, no randomness, no network, no vendor SDK.
The real-LLM provider produces richer prose from the same structured prompt; this
fallback guarantees the *structure* and grounding even offline. Nothing here is
invented — every rendered skill is matched from the retrieved evidence text.
"""

from __future__ import annotations

import re

# --------------------------------------------------------------------------- #
# Category → keyword taxonomy
# --------------------------------------------------------------------------- #
# Ordered so the per-document sections read top-down (languages first, tooling
# last). Each category maps a *canonical display term* to its surface aliases;
# matching is case-insensitive and whole-token (so "SQL" does not fire inside
# "MySQL", and "Java" does not fire inside "JavaScript"). Extend by adding terms
# here — no other code changes required.
_CATEGORY_DEFS: list[tuple[str, dict[str, list[str]]]] = [
    ("Languages", {
        "Python": [], "SQL": [], "JavaScript": ["JS"], "TypeScript": ["TS"],
        "Java": [], "C++": [], "MATLAB": [], "Bash": [], "Kotlin": [],
        "Swift": [], "Scala": [], "PHP": [],
    }),
    ("Frontend", {
        "React": ["React.js"], "Next.js": ["NextJS"], "Vue": ["Vue.js"],
        "Angular": [], "HTML": [], "CSS": [], "Tailwind": ["TailwindCSS"],
        "Redux": [], "Vite": [], "Streamlit": [],
    }),
    ("Backend", {
        "FastAPI": [], "Flask": [], "Django": [], "Node.js": ["NodeJS"],
        "Express": ["Express.js"], "Spring Boot": ["Spring"],
        "REST API": ["REST APIs", "RESTful"], "GraphQL": [], "gRPC": [],
        "JWT": [], "WebSocket": ["WebSockets"], "Microservices": [],
    }),
    ("Databases / Storage", {
        "MongoDB": ["Mongo"], "PostgreSQL": ["Postgres"], "MySQL": [],
        "Redis": [], "SQLite": [], "Oracle": [], "Qdrant": [], "FAISS": [],
        "Pinecone": [], "Chroma": ["ChromaDB"], "Weaviate": [],
        "Elasticsearch": [], "MinIO": [],
        "vector search": [], "vector database": ["vector databases", "vector store", "vector DB"],
    }),
    ("AI / Machine Learning", {
        "PyTorch": [], "TensorFlow": [], "Scikit-Learn": ["Scikit-learn", "sklearn"],
        "Keras": [], "Hugging Face": ["HuggingFace"], "Transformers": [],
        "LangGraph": [], "LangChain": [], "LlamaIndex": [],
        "RAG": ["retrieval-augmented generation"],
        "LLM": ["LLMs", "large language model", "large language models"],
        "YOLOv8": ["YOLO"], "OpenCV": [], "NLP": [], "computer vision": [],
        "OpenAI": [], "embeddings": ["embedding"],
        "fine-tuning": ["fine-tune", "fine-tuned"], "prompt engineering": [],
    }),
    ("Cloud / Deployment", {
        "AWS": [], "GCP": ["Google Cloud"], "Azure": [], "Docker": [],
        "Kubernetes": ["K8s"], "Terraform": [], "CI/CD": [],
        "GitHub Actions": [], "Jenkins": [], "Caddy": [], "Nginx": [],
        "Lambda": ["AWS Lambda"], "EC2": [], "S3": ["AWS S3"], "Vercel": [],
        "Netlify": [],
    }),
    ("Analytics / Automation", {
        "Pandas": [], "NumPy": ["Numpy"], "Power BI": ["PowerBI"], "Tableau": [],
        "QuickSight": ["AWS QuickSight"], "Excel": [], "n8n": [], "Airflow": [],
        "Zapier": [], "Matplotlib": [], "Seaborn": [], "Plotly": [], "dbt": [],
        "Spark": ["PySpark", "Apache Spark"],
    }),
    ("Observability / Evaluation", {
        "LangSmith": [], "Promptfoo": [], "PostHog": [], "Prometheus": [],
        "Grafana": [], "Sentry": [], "OpenTelemetry": [],
        "Weights & Biases": ["wandb", "W&B"], "MLflow": [],
    }),
]

CATEGORY_ORDER: list[str] = [name for name, _ in _CATEGORY_DEFS]

# A short concept phrase per category, used to phrase similarities/differences as
# concepts ("both include analytics and automation") rather than token lists.
_CATEGORY_CONCEPT: dict[str, str] = {
    "Languages": "core programming languages",
    "Frontend": "frontend development",
    "Backend": "backend APIs",
    "Databases / Storage": "data storage and retrieval",
    "AI / Machine Learning": "AI/ML engineering",
    "Cloud / Deployment": "cloud and deployment",
    "Analytics / Automation": "analytics and automation",
    "Observability / Evaluation": "observability and evaluation",
}


def _compile(surface: str) -> re.Pattern:
    # Whole-token, case-insensitive; internal spaces match any run of whitespace
    # (so wrapped/collapsed lines still match), boundaries reject alnum neighbours
    # so short terms do not fire inside larger words.
    escaped = re.escape(surface).replace(r"\ ", r"\s+")
    return re.compile(rf"(?<![A-Za-z0-9]){escaped}(?![A-Za-z0-9])", re.IGNORECASE)


# (category, canonical, [compiled patterns]) — definition order is output order.
_TERM_TABLE: list[tuple[str, str, list[re.Pattern]]] = [
    (category, canonical, [_compile(s) for s in [canonical, *aliases]])
    for category, terms in _CATEGORY_DEFS
    for canonical, aliases in terms.items()
]

# Content that is not a technical skill and should never surface in a
# technical-skills comparison (contact, education, extracurricular, biography,
# headers/honorifics). Used to drop noisy statements/project lines.
_EXCLUDE_RE = re.compile(
    r"[\w.+-]+@[\w-]+\.[\w.-]+"                       # email
    r"|\b(?:linkedin|github\.com|portfolio|https?://|www\.)"
    r"|\b(?:b\.?tech|bachelor|master'?s|university|college|cgpa|gpa|semester"
    r"|10th|12th|high school|schooling|degree|coursework)\b"
    r"|\b(?:boxing|hospitality|artist|volunteer|hobbies|interests|extracurricular"
    r"|sports|ncc|captain of|declaration|date of birth|marital)\b"
    r"|\b(?:mr|ms|mrs)\.\s|\bcurriculum vitae\b|\breferences available\b"
    r"|\bfather'?s name\b|\bmother'?s name\b",
    re.IGNORECASE,
)

_IMPL_VERB_RE = re.compile(
    r"\b(?:built|build|developed|develop|implemented|implement|designed|design"
    r"|deployed|deploy|engineered|created|create|integrated|automated|optimized"
    r"|optimised|trained|fine-tuned|architected|orchestrated)\b",
    re.IGNORECASE,
)

_TECHNICAL_QUERY_RE = re.compile(
    r"\b(?:technical|tech stack|technolog|skill|stack|framework|language|tooling"
    r"|engineering|proficien|expertise)\b",
    re.IGNORECASE,
)

# Deterministic bounds so the answer stays compact regardless of chunk volume.
MAX_TERMS_PER_CATEGORY = 12
MAX_SIMILARITY_LINES = 6
MAX_PROJECT_LINES = 2
MAX_STATEMENT_LEN = 200
MAX_UNIQUE_TERMS_IN_LINE = 8


# --------------------------------------------------------------------------- #
# Normalization / helpers
# --------------------------------------------------------------------------- #

def normalize_text(text: str) -> str:
    """Collapse whitespace and merge chunk-wrapped lines into one clean string."""
    return re.sub(r"\s+", " ", str(text or "")).strip()


def is_technical_query(query: str) -> bool:
    return bool(_TECHNICAL_QUERY_RE.search(query or ""))


def is_excluded(statement: str) -> bool:
    return bool(_EXCLUDE_RE.search(statement or ""))


def extract_skills(text: str) -> dict[str, list[str]]:
    """Extract canonical technical skills from text, grouped by category in
    taxonomy order. Only non-empty categories are returned; each term appears
    once (natural de-duplication across repeated/overlapping chunks)."""
    normalized = normalize_text(text)
    result: dict[str, list[str]] = {}
    for category, canonical, patterns in _TERM_TABLE:
        if any(p.search(normalized) for p in patterns):
            bucket = result.setdefault(category, [])
            if canonical not in bucket and len(bucket) < MAX_TERMS_PER_CATEGORY:
                bucket.append(canonical)
    return result


def _all_terms(skills: dict[str, list[str]]) -> list[str]:
    terms: list[str] = []
    for category in CATEGORY_ORDER:
        for term in skills.get(category, []):
            if term not in terms:
                terms.append(term)
    return terms


def _project_lines(text: str) -> list[str]:
    """Concise implementation/project statements: sentences that pair an action
    verb with a technical term, de-duplicated, filtered of biographical noise,
    length-bounded. Grounded in the evidence — nothing is synthesized."""
    normalized = normalize_text(text)
    lines: list[str] = []
    seen: set[str] = set()
    for raw in re.split(r"(?<=[.;!?])\s+|•|\n", normalized):
        sentence = raw.strip(" -•\t")
        if not sentence or len(sentence) < 12:
            continue
        if is_excluded(sentence) or not _IMPL_VERB_RE.search(sentence):
            continue
        if not any(p.search(sentence) for _, _, ps in _TERM_TABLE for p in ps):
            continue
        if len(sentence) > MAX_STATEMENT_LEN:
            sentence = sentence[: MAX_STATEMENT_LEN - 1].rstrip() + "…"
        key = sentence.lower()
        if key in seen:
            continue
        seen.add(key)
        lines.append(sentence)
        if len(lines) >= MAX_PROJECT_LINES:
            break
    return lines


def _source_label(filename: str, page) -> str:
    return f"{filename} p.{page}" if page is not None else str(filename)


# --------------------------------------------------------------------------- #
# Synthesis
# --------------------------------------------------------------------------- #

def compose_comparison(
    *,
    query: str,
    documents: list[str],
    evidence: list[dict],
) -> str:
    """Compose the deterministic comparison answer.

    ``documents`` is the ordered list of selected filenames (every one is
    represented, even with no evidence). ``evidence`` is an ordered list of
    ``{"filename", "page", "content"}`` dicts. Technical-skills comparisons render
    category sections; if no technical evidence is present at all, a compact
    de-duplicated statement comparison is used instead. No raw chunk is dumped."""
    grouped: dict[str, list[dict]] = {}
    for item in evidence:
        fn = item.get("filename")
        if fn:
            grouped.setdefault(fn, []).append(item)
    for fn in grouped:
        if fn not in documents:
            documents = [*documents, fn]

    doc_skills = {
        fn: extract_skills(" ".join(i.get("content", "") for i in grouped.get(fn, [])))
        for fn in documents
    }
    any_technical = any(doc_skills[fn] for fn in documents)

    header = [f"Comparison of the selected documents for: {query}", ""]
    if is_technical_query(query) or any_technical:
        body = _technical_body(documents, grouped, doc_skills)
    else:
        body = _statement_body(documents, grouped)

    sources = _sources_block(evidence)
    return "\n".join([*header, *body, *sources]).rstrip()


def _technical_body(documents, grouped, doc_skills) -> list[str]:
    lines: list[str] = []
    for index, fn in enumerate(documents, start=1):
        lines.append(f"Document {index} — {fn}")
        skills = doc_skills.get(fn) or {}
        projects = _project_lines(" ".join(i.get("content", "") for i in grouped.get(fn, [])))
        if not skills and not projects:
            lines.append(f"No relevant technical-skill evidence was found in {fn}.")
            lines.append("")
            continue
        for category in CATEGORY_ORDER:
            terms = skills.get(category)
            if not terms:
                continue
            lines.append("")
            lines.append(category)
            for term in terms:
                lines.append(f"- {term}")
        if projects:
            lines.append("")
            lines.append("Technical Projects")
            for project in projects:
                lines.append(f"- {project}")
        lines.append("")

    with_skills = [fn for fn in documents if doc_skills.get(fn)]
    lines.extend(_similarities(with_skills, doc_skills))
    lines.append("")
    lines.extend(_differences(documents, doc_skills))
    lines.append("")
    return lines


def _similarities(with_skills, doc_skills) -> list[str]:
    lines = ["Similarities"]
    if len(with_skills) < 2:
        lines.append(
            "A similarity comparison requires technical evidence from at least two "
            "documents; it is not available for this request."
        )
        return lines

    term_sets = [set(_all_terms(doc_skills[fn])) for fn in with_skills]
    shared_terms = [t for t in _all_terms(doc_skills[with_skills[0]]) if all(t in s for s in term_sets)]
    cat_sets = [set(doc_skills[fn].keys()) for fn in with_skills]
    shared_cats = [c for c in CATEGORY_ORDER if all(c in s for s in cat_sets)]

    emitted = 0
    if shared_terms:
        listed = ", ".join(shared_terms[:MAX_UNIQUE_TERMS_IN_LINE])
        lines.append(f"- Both documents list {listed}.")
        emitted += 1
    for cat in shared_cats:
        if emitted >= MAX_SIMILARITY_LINES:
            break
        # Skip a category already fully conveyed by the shared-term line.
        if shared_terms and cat == "Languages" and set(doc_skills[with_skills[0]].get(cat, [])) <= set(shared_terms):
            continue
        lines.append(f"- Both include {_CATEGORY_CONCEPT.get(cat, cat.lower())}.")
        emitted += 1
    if emitted == 0:
        lines.append(
            "- The documents share no overlapping technical skills in the retrieved evidence."
        )
    return lines


def _differences(documents, doc_skills) -> list[str]:
    lines = ["Differences"]
    with_skills = [fn for fn in documents if doc_skills.get(fn)]
    emitted = 0
    for fn in documents:
        skills = doc_skills.get(fn)
        if not skills:
            lines.append(f"- No relevant technical-skill evidence was found in {fn}.")
            emitted += 1
            continue
        others = set()
        for other in with_skills:
            if other != fn:
                others |= set(_all_terms(doc_skills[other]))
        unique = [t for t in _all_terms(skills) if t not in others]
        unique_cats = [c for c in CATEGORY_ORDER if c in skills and all(c not in doc_skills.get(o, {}) for o in with_skills if o != fn)]
        if unique:
            listed = ", ".join(unique[:MAX_UNIQUE_TERMS_IN_LINE])
            if unique_cats:
                concepts = ", ".join(_CATEGORY_CONCEPT.get(c, c.lower()) for c in unique_cats[:3])
                lines.append(f"- {fn} additionally covers {concepts}: {listed}.")
            else:
                lines.append(f"- Only {fn} lists {listed}.")
            emitted += 1
    if emitted == 0:
        lines.append("- The documents cover the same technical skills in the retrieved evidence.")
    return lines


def _statement_body(documents, grouped) -> list[str]:
    """Fallback for a non-technical comparison: compact, de-duplicated statements
    per document (never a raw chunk dump), plus honest similarity/difference
    notes. Only reached when no technical evidence and a non-technical query."""
    lines: list[str] = []
    for index, fn in enumerate(documents, start=1):
        lines.append(f"Document {index} — {fn}")
        statements = _statements(grouped.get(fn, []))
        if not statements:
            lines.append(f"No relevant evidence was found in {fn}.")
        else:
            for statement, label in statements:
                lines.append(f"- {statement} ({label})")
        lines.append("")
    with_evidence = [fn for fn in documents if grouped.get(fn)]
    lines.append("Similarities")
    lines.append(
        "- Both documents contain content relevant to the request; see the "
        "per-document sections above."
        if len(with_evidence) >= 2
        else "- A comparison requires evidence from at least two documents."
    )
    lines.append("")
    lines.append("Differences")
    for fn in documents:
        if not grouped.get(fn):
            lines.append(f"- No relevant evidence was found in {fn}.")
    if all(grouped.get(fn) for fn in documents):
        lines.append("- Each document's distinct points are listed in its section above.")
    lines.append("")
    return lines


def _statements(items) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for item in items:
        for raw in re.split(r"(?<=[.;!?])\s+|•|\n", normalize_text(item.get("content", ""))):
            sentence = raw.strip(" -•\t")
            if len(sentence) < 12 or is_excluded(sentence):
                continue
            if len(sentence) > MAX_STATEMENT_LEN:
                sentence = sentence[: MAX_STATEMENT_LEN - 1].rstrip() + "…"
            key = sentence.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append((sentence, _source_label(item.get("filename"), item.get("page"))))
            if len(out) >= 4:
                return out
    return out


def _sources_block(evidence) -> list[str]:
    lines = ["Sources"]
    seen: list[str] = []
    for item in evidence:
        fn = item.get("filename")
        if not fn:
            continue
        label = _source_label(fn, item.get("page"))
        if label not in seen:
            seen.append(label)
            lines.append(f"- {label}")
    if not seen:
        lines.append("- No sources were available.")
    return lines
