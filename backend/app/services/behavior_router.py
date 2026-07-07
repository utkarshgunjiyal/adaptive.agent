import re

from app.schemas.request_plan import RequestPlan, RequestFilters


def extract_page_number(question: str) -> int | None:
    match = re.search(r"\bpage\s+(\d+)\b", question.lower())
    if match:
        return int(match.group(1))
    return None


def extract_page_range(question: str) -> tuple[int | None, int | None]:
    match = re.search(r"\bpage\s+(\d+)\s*(?:to|-)\s*(?:page\s*)?(\d+)\b", question.lower())
    if match:
        return int(match.group(1)), int(match.group(2))
    return None, None


def create_request_plan(question: str) -> RequestPlan:
    q = question.lower()

    page = extract_page_number(question)
    page_start, page_end = extract_page_range(question)

    if any(phrase in q for phrase in ["remember that", "from now on", "i prefer"]):
        return RequestPlan(
            intent="preference",
            operation="update",
            hitl=True,
            confidence=0.95,
            route_reason="Preference update phrase detected.",
        )

    if any(phrase in q for phrase in ["what did we decide", "what did we discuss", "what have we decided"]):
        return RequestPlan(
            intent="memory",
            operation="lookup",
            filters=RequestFilters(topic=question),
            confidence=0.9,
            route_reason="History/decision lookup phrase detected.",
        )

    if any(phrase in q for phrase in ["full summary", "overall summary", "summarize this pdf", "summarize this document"]):
        return RequestPlan(
            intent="document",
            operation="summarize",
            confidence=0.9,
            route_reason="Whole-document summary phrase detected.",
        )

    if page and any(word in q for word in ["summarize", "summary"]):
        return RequestPlan(
            intent="document",
            operation="summarize",
            filters=RequestFilters(page=page),
            confidence=0.95,
            route_reason="Page summary detected.",
        )

    if page_start and page_end:
        return RequestPlan(
            intent="document",
            operation="compare" if "compare" in q else "qa",
            filters=RequestFilters(page_start=page_start, page_end=page_end, topic=question),
            confidence=0.9,
            route_reason="Page range detected.",
        )

    if any(word in q for word in ["pdf", "document", "file", "page", "section", "chapter"]):
        return RequestPlan(
            intent="document",
            operation="qa",
            filters=RequestFilters(page=page, topic=question),
            confidence=0.75,
            route_reason="Document-related word detected.",
        )

    return RequestPlan(
        intent="general",
        operation="qa",
        filters=RequestFilters(topic=question),
        confidence=0.7,
        route_reason="Default general request.",
    )