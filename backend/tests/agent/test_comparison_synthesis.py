"""Phase 44.2 — Evidence Compression & Comparison Synthesis.

Exercises the REAL deterministic provider path (offline, credential-free): the
comparison answer must compress retrieved chunks into concise, category-grouped
technical skills — no raw chunk dumps, no duplicate bullets, no biographical
noise, no opaque E# citations — with concept-based similarities/differences and
filename+page sources. Also unit-tests the pure synthesis helpers directly.

Config-free: FinalPrompts are built by FinalContextBuilder from hand-made
RunContexts; generation uses DeterministicFinalProvider. No Mongo/Qdrant/Redis,
no application settings, no real/paid LLM.
"""

import asyncio

from app.agent.context.final_builder import FinalContextBuilder
from app.agent.llm import comparison_synthesis as cs
from app.agent.llm.final_provider import DeterministicFinalProvider
from app.agent.runtime.context import EvidenceItem, RunContext


def run(coro):
    return asyncio.run(coro)


# Representative of the two observed résumés (compressed to the salient lines,
# with realistic biographical/contact/education noise mixed in).
RESUME_1 = (
    "Contact: fresher@example.com | linkedin.com/in/fresher | +91 98765 43210\n"
    "Education: B.Tech in Computer Science, CGPA 8.4.\n"
    "Skills: Python, SQL, Pandas, NumPy, MySQL for data analysis.\n"
    "Built interactive dashboards in Power BI and AWS QuickSight.\n"
    "Extracurricular: district-level boxing, artist hospitality volunteer.\n"
    "Mr. Fresher served as captain of the college club."
)
RESUME_2 = (
    "Skills: Python, JavaScript, FastAPI, React, MongoDB.\n"
    "Developed a RAG pipeline with vector search over Qdrant and LangGraph.\n"
    "Trained models in PyTorch and deployed services with Docker on AWS.\n"
    "Education: B.Tech, University of Example."
)


def _comparison_prompt(evidence, documents,
                       request="Compare the technical skills in these two documents."):
    rc = RunContext.create(request, user_id="u", thread_id="t1")
    for e in evidence:
        rc.append_evidence(e)
    rc.metadata["interpretation"] = {"intents": ["document_comparison"]}
    rc.metadata["document_scope"] = {
        "status": "resolved",
        "resolved_document_ids": [d["document_id"] for d in documents],
        "documents": documents,
    }
    return FinalContextBuilder().build(rc)


def _ev(filename, doc_id, content, page=1, score=0.9):
    return EvidenceItem(source=f"document:{filename}", content=content, score=score,
                        metadata={"filename": filename, "page": page, "document_id": doc_id})


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #

def test_extract_skills_groups_by_category():
    skills = cs.extract_skills("Built a FastAPI service in Python with a React frontend and MongoDB.")
    assert skills["Languages"] == ["Python"]
    assert skills["Backend"] == ["FastAPI"]
    assert skills["Frontend"] == ["React"]
    assert skills["Databases / Storage"] == ["MongoDB"]


def test_extract_skills_excludes_non_technical():
    skills = cs.extract_skills(RESUME_1)
    flat = [t for terms in skills.values() for t in terms]
    # Real skills present…
    assert "Python" in flat and "Pandas" in flat and "Power BI" in flat
    # …biography/contact/education never becomes a "skill".
    assert "boxing" not in " ".join(flat).lower()


def test_extract_skills_deduplicates_repeated_chunks():
    text = "Python FastAPI. " * 5  # same skills repeated across overlapping chunks
    skills = cs.extract_skills(text)
    assert skills["Languages"] == ["Python"]
    assert skills["Backend"] == ["FastAPI"]


def test_normalize_merges_wrapped_lines():
    # A skill list wrapped across a chunk boundary must still be recognized.
    text = "Skills: Python,\nFastAPI, React,\nMongoDB"
    skills = cs.extract_skills(text)
    assert "FastAPI" in skills["Backend"]
    assert "React" in skills["Frontend"]
    assert "MongoDB" in skills["Databases / Storage"]


def test_short_terms_do_not_fire_inside_larger_words():
    # "SQL" must not match inside "MySQL"; "Java" not inside "JavaScript".
    skills = cs.extract_skills("MySQL and JavaScript only.")
    assert skills.get("Languages", []) == ["JavaScript"]
    assert skills["Databases / Storage"] == ["MySQL"]


# --------------------------------------------------------------------------- #
# Provider output
# --------------------------------------------------------------------------- #

def _demo_text():
    prompt = _comparison_prompt(
        evidence=[
            _ev("resumeresume.pdf", "d1", RESUME_1, page=1),
            _ev("my_final_resume.pdf", "d2", RESUME_2, page=1),
        ],
        documents=[
            {"document_id": "d1", "filename": "resumeresume.pdf"},
            {"document_id": "d2", "filename": "my_final_resume.pdf"},
        ],
    )
    return run(DeterministicFinalProvider().generate(prompt)).text


def test_both_documents_represented_with_expected_terms():
    text = _demo_text()
    assert "Document 1 — resumeresume.pdf" in text
    assert "Document 2 — my_final_resume.pdf" in text
    # resumeresume.pdf (analytics-focused)
    d1 = text.split("Document 1", 1)[1].split("Document 2", 1)[0]
    assert "Python" in d1
    assert "Pandas" in d1 or "NumPy" in d1
    assert "MySQL" in d1
    assert "Power BI" in d1 or "QuickSight" in d1
    # my_final_resume.pdf (AI/backend-focused)
    d2 = text.split("Document 2", 1)[1].split("Similarities", 1)[0]
    assert "FastAPI" in d2
    assert "React" in d2
    assert "MongoDB" in d2
    assert "RAG" in d2 or "vector search" in d2
    assert "LangGraph" in d2 or "PyTorch" in d2


def test_no_raw_chunk_dump_or_biographical_noise():
    text = _demo_text()
    lowered = text.lower()
    for noise in ("boxing", "artist hospitality", "cgpa", "linkedin", "@example.com", "mr. fresher"):
        assert noise not in lowered, noise


def test_no_opaque_citation_ids_in_output():
    text = _demo_text()
    # No bare evidence ids (E1, E7, …) anywhere in user-facing text.
    import re
    assert not re.search(r"\bE\d+\b", text), text
    assert "[E" not in text


def test_filename_page_sources_present_and_deduped():
    text = _demo_text()
    sources = text.split("Sources", 1)[1]
    assert "resumeresume.pdf p.1" in sources
    assert "my_final_resume.pdf p.1" in sources
    # De-duplicated: each source label appears once.
    assert sources.count("resumeresume.pdf p.1") == 1


def test_meaningful_shared_concept():
    text = _demo_text()
    sims = text.split("Similarities", 1)[1].split("Differences", 1)[0]
    # Both résumés list Python — a real shared technical concept, not a token list.
    assert "Python" in sims
    assert "Both" in sims


def test_meaningful_category_differences():
    text = _demo_text()
    diffs = text.split("Differences", 1)[1].split("Sources", 1)[0]
    # Analytics/BI only in résumé 1; AI/backend only in résumé 2.
    assert "resumeresume.pdf" in diffs and "my_final_resume.pdf" in diffs
    assert "Power BI" in diffs or "analytics" in diffs.lower()
    assert "FastAPI" in diffs or "AI/ML" in diffs


def test_output_length_bounded_regardless_of_chunk_volume():
    # A huge, repetitive chunk must not inflate the answer — extraction is by term.
    big = ("Python and FastAPI and MongoDB. " * 400)
    prompt = _comparison_prompt(
        evidence=[
            _ev("a.pdf", "d1", big, page=1),
            _ev("b.pdf", "d2", "React and Redis and Docker.", page=1),
        ],
        documents=[{"document_id": "d1", "filename": "a.pdf"},
                   {"document_id": "d2", "filename": "b.pdf"}],
    )
    text = run(DeterministicFinalProvider().generate(prompt)).text
    assert len(text) < 1500  # far smaller than the ~12k-char raw evidence
    assert text.count("- Python") <= 1  # not repeated per chunk


def test_empty_evidence_document_stated_explicitly():
    prompt = _comparison_prompt(
        evidence=[_ev("resumeresume.pdf", "d1", RESUME_1, page=1)],
        documents=[{"document_id": "d1", "filename": "resumeresume.pdf"},
                   {"document_id": "d2", "filename": "my_final_resume.pdf"}],
    )
    text = run(DeterministicFinalProvider().generate(prompt)).text
    assert "No relevant technical-skill evidence was found in my_final_resume.pdf." in text


def test_no_unsupported_skills_invented():
    # Only skills present in the evidence may appear. Evidence mentions Python only.
    prompt = _comparison_prompt(
        evidence=[
            _ev("a.pdf", "d1", "Skilled in Python programming.", page=1),
            _ev("b.pdf", "d2", "Skilled in Python programming.", page=1),
        ],
        documents=[{"document_id": "d1", "filename": "a.pdf"},
                   {"document_id": "d2", "filename": "b.pdf"}],
    )
    text = run(DeterministicFinalProvider().generate(prompt)).text
    for absent in ("FastAPI", "React", "Kubernetes", "PyTorch", "MongoDB"):
        assert absent not in text, absent


def test_streaming_equals_non_streaming():
    prompt = _comparison_prompt(
        evidence=[
            _ev("resumeresume.pdf", "d1", RESUME_1, page=1),
            _ev("my_final_resume.pdf", "d2", RESUME_2, page=1),
        ],
        documents=[{"document_id": "d1", "filename": "resumeresume.pdf"},
                   {"document_id": "d2", "filename": "my_final_resume.pdf"}],
    )
    provider = DeterministicFinalProvider()

    async def _stream():
        return "".join([c async for c in provider.generate_stream(prompt)])

    assert run(_stream()) == run(provider.generate(prompt)).text


# --------------------------------------------------------------------------- #
# Non-comparison output stays unchanged
# --------------------------------------------------------------------------- #

def test_non_comparison_answer_unchanged():
    rc = RunContext.create("What does the document say about pricing?", user_id="u", thread_id="t1")
    rc.append_evidence(EvidenceItem(source="document:pricing.pdf", content="The price is $10 per month.",
                                    score=0.9, metadata={"filename": "pricing.pdf", "page": 2, "document_id": "d1"}))
    # Single document → not a comparison.
    rc.metadata["document_scope"] = {"status": "resolved", "documents": [
        {"document_id": "d1", "filename": "pricing.pdf"}]}
    prompt = FinalContextBuilder().build(rc)
    assert prompt.metadata["is_comparison"] is False
    text = run(DeterministicFinalProvider().generate(prompt)).text
    assert text.startswith("Based on the available context, here is the answer to:")
    assert "Document 1 —" not in text
