"""Write tool: save_user_preference.

This is the first tool with ``risk_level = "write"``. It exists to exercise
the approval workflow end to end:

1. The planner may include a step that calls this tool.
2. The policy validator flags the step as ``requires_approval``.
3. The agent run pauses in state ``waiting_approval`` and emits an
   ``approval_request`` SSE frame with the pending step details.
4. The frontend renders an approval card (see ``ApprovalCard`` component).
5. When the user hits Approve, ``/api/agent/runs/{run_id}/approve`` resumes
   the run and this executor is called.

The preferences themselves are stored in ``db.user_preferences`` with a
compound unique index on ``(user_id, key)``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.db import get_db
from app.models import ToolBadge


async def save_user_preference(*, user_id: str, key: str, value: Any,
                               **_: Any) -> dict[str, Any]:
    key = str(key or "").strip()[:100]
    if not key:
        return {"summary": "Missing preference key.", "evidence": [], "error": True}

    now = datetime.now(timezone.utc)
    await get_db().user_preferences.update_one(
        {"user_id": user_id, "key": key},
        {"$set": {
            "user_id": user_id,
            "key": key,
            "value": value,
            "updated_at": now,
        }, "$setOnInsert": {"created_at": now}},
        upsert=True,
    )
    return {
        "summary": f"Saved preference {key} → {value!r}.",
        "evidence": [{
            "source_type": ToolBadge.CONTEXT.value,
            "title": f"Preference saved · {key}",
            "snippet": f"{key} = {value}",
        }],
    }


async def get_user_preferences(*, user_id: str, **_: Any) -> dict[str, Any]:
    cursor = get_db().user_preferences.find({"user_id": user_id})
    prefs = {}
    async for row in cursor:
        prefs[row["key"]] = row.get("value")
    return {
        "summary": f"Loaded {len(prefs)} preference(s): {', '.join(prefs.keys()) or 'none'}",
        "evidence": [
            {"source_type": ToolBadge.CONTEXT.value,
             "title": f"Preference · {k}",
             "snippet": f"{k} = {v}"}
            for k, v in prefs.items()
        ],
        "preferences": prefs,
    }
