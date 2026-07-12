"""Thread-scoped execution resource state (Phase 46.3.2).

After a successful tool execution the runtime publishes the resources it resolved
(owner/repo/issue_number/… — provider-neutral ``Resource`` objects) into a
thread-scoped store, so a later request in the SAME thread can resolve them with no
LLM. State is keyed by ``(user_id, thread_id, provider)`` — isolation across users
and threads is structural, not best-effort.

``ThreadResourceStore`` is an in-memory, config-free default (a Mongo-backed store
can implement the same tiny surface later). ``ResourcePublisher`` is the
post-success seam the runtime calls; it only ever publishes on a successful result
and never publishes tokens/headers/values-as-secrets — resources are safe
identifiers already vetted by the resolver.
"""

from __future__ import annotations

from app.agent.resources.models import Resource, ResourceSource


class ThreadResourceStore:
    """In-memory ``(user_id, thread_id, provider) -> {type: Resource}`` store."""

    def __init__(self) -> None:
        self._data: dict[tuple[str, str, str], dict[str, Resource]] = {}

    @staticmethod
    def _key(user_id, thread_id, provider) -> tuple[str, str, str] | None:
        if not (user_id and thread_id and provider):
            return None  # no thread/user/provider scope → never stored or read
        return (str(user_id), str(thread_id), str(provider))

    def publish(self, user_id, thread_id, provider, resources: list[Resource]) -> list[str]:
        """Merge ``resources`` into the thread's state (latest wins per type).

        Returns the resource TYPES stored (for safe diagnostics). A no-op (empty
        return) when the scope is incomplete or there is nothing to store.
        """
        key = self._key(user_id, thread_id, provider)
        if key is None or not resources:
            return []
        bucket = self._data.setdefault(key, {})
        for r in resources:
            bucket[r.type] = r
        return sorted(bucket.keys())

    def view(self, user_id, thread_id, provider) -> list[Resource]:
        """Remembered resources for this exact (user, thread, provider) scope."""
        key = self._key(user_id, thread_id, provider)
        if key is None:
            return []
        return list(self._data.get(key, {}).values())

    def remembered(self, user_id, thread_id, provider) -> dict[str, str | int]:
        """``{type: value}`` view for a resolver's cross-turn fill."""
        return {r.type: r.value for r in self.view(user_id, thread_id, provider)}


class ResourcePublisher:
    """Post-success publication seam (the DirectRuntime ``execution_observer``).

    Publishes the resources the resolver produced for a SUCCESSFUL call, tagged as
    ``PRIOR_OUTPUT`` (they now originate from a prior successful output). Never
    publishes on failure, and never on a missing/ambiguous build (those never
    execute). Best-effort: a publication error can never affect a run.
    """

    def __init__(self, store: ThreadResourceStore) -> None:
        self._store = store

    def __call__(self, tool, build, result, run_context) -> list[str]:
        if not getattr(result, "success", False):
            return []
        from app.agent.resources.resolver import provider_of

        provider = provider_of(tool)
        published = list(getattr(build, "published_resources", None) or [])
        if not provider or not published:
            return []
        resources = [
            Resource(type=str(r["type"]), value=r["value"],
                     source=ResourceSource.PRIOR_OUTPUT, provider=provider)
            for r in published
            if isinstance(r, dict) and "type" in r and "value" in r
        ]
        return self._store.publish(
            getattr(run_context, "user_id", None),
            getattr(run_context, "thread_id", None),
            provider, resources,
        )
