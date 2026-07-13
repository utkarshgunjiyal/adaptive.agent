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

    Publishes the resources a SUCCESSFUL call established, tagged ``PRIOR_OUTPUT``.
    Two trusted sources are merged, with the **normalized output authoritative**:

    - the resources RESOLVED for the call (from ``build.published_resources``), and
    - resources EXTRACTED from the trusted normalized output by a per-provider
      ``ProviderResourceExtractor`` (Phase 46.3.2.1) — e.g. the active repository's
      owner+repo from ``search_repositories`` output, which the resolver could not
      know pre-execution when the connector identity is unknown.

    Never publishes on failure, and never on a missing/ambiguous build (those never
    execute). Best-effort: a publication error can never affect a run.
    """

    def __init__(self, store: ThreadResourceStore, *, extractors=None) -> None:
        self._store = store
        self._extractors = extractors

    def __call__(self, tool, build, result, run_context) -> list[str]:
        if not getattr(result, "success", False):
            return []
        from app.agent.resources.resolver import provider_of

        provider = provider_of(tool)
        if not provider:
            return []

        # Base: resources resolved for the call (pre-execution).
        merged: dict[str, Resource] = {}
        for r in getattr(build, "published_resources", None) or []:
            if isinstance(r, dict) and "type" in r and "value" in r:
                merged[str(r["type"])] = Resource(
                    type=str(r["type"]), value=r["value"],
                    source=ResourceSource.PRIOR_OUTPUT, provider=provider)

        # Trusted output overrides/augments (authoritative for the active resource).
        extractor = self._extractors.for_provider(provider) if self._extractors else None
        if extractor is not None:
            resolved_values = {t: res.value for t, res in merged.items()}
            output = getattr(result, "output", None) or {}
            for r in extractor.extract(tool, resolved_values, output) or []:
                merged[r.type] = r

        if not merged:
            return []
        return self._store.publish(
            getattr(run_context, "user_id", None),
            getattr(run_context, "thread_id", None),
            provider, list(merged.values()),
        )
