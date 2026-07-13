"""Unified Capability Registry (Phase 40).

One capability platform. Individual capability sources (internal, MCP, future)
are *mounted* into a single ``UnifiedCapabilityRegistry`` that owns one shared
``ToolRegistry`` — the exact object the existing hybrid retrieval reads. So the
planner, retriever, execution bridge, evaluator, repair, and orchestrator never
know a capability's origin; everything is a ``ToolSpec``.

The unified registry owns:
- **registration** into the shared ``ToolRegistry``,
- **duplicate / collision detection** (no id may belong to two sources),
- **namespace isolation** (strict sources emit ids under ``<namespace>.``; no
  source may claim another's namespace),
- **source ownership** (``source_id -> {ids}``; unmount/refresh only ever touch a
  source's own ids),
- **atomic refresh** (a new batch is fully validated *before* any mutation; a
  discovery/validation failure leaves the previous capabilities active),
- **lifecycle** (``shutdown`` closes every source).

Config-free: no LLM, no DB, no settings. Sources are injected.
"""

from __future__ import annotations

from app.agent.models.tool_spec import ToolSpec
from app.agent.registry.registry import ToolRegistry
from app.agent.registry.sources import CapabilitySource


class UnifiedRegistryError(Exception):
    """Base error for the unified capability registry."""


class DuplicateSourceError(UnifiedRegistryError):
    """Raised when mounting a source_id (or namespace) that is already mounted."""


class UnknownSourceError(UnifiedRegistryError):
    """Raised when operating on a source_id that is not mounted."""


class NamespaceViolationError(UnifiedRegistryError):
    """Raised when a strict source emits an id outside its own namespace."""


class CapabilityCollisionError(UnifiedRegistryError):
    """Raised when a source's id collides with another source's capability."""


class UnifiedCapabilityRegistry:
    """Mounts capability sources into one shared ToolRegistry."""

    def __init__(self, registry: ToolRegistry | None = None) -> None:
        self._registry = registry or ToolRegistry()
        self._sources: dict[str, CapabilitySource] = {}
        self._owned: dict[str, list[str]] = {}      # source_id -> [tool_ids]
        self._owner: dict[str, str] = {}            # tool_id -> source_id
        self._namespaces: dict[str, str] = {}       # namespace -> source_id

    # -- Read surface --------------------------------------------------------

    @property
    def tool_registry(self) -> ToolRegistry:
        """The shared registry the hybrid retriever consumes (unchanged interface)."""
        return self._registry

    def list(self) -> list[ToolSpec]:
        return self._registry.list_all()

    def resolve(self, capability_id: str) -> ToolSpec | None:
        return self._registry.get(capability_id) if self._registry.exists(capability_id) else None

    def list_sources(self) -> list[dict]:
        return [
            {
                "source_id": sid,
                "namespace": self._sources[sid].namespace,
                "tool_kind": self._sources[sid].tool_kind.value,
                "capabilities": len(self._owned.get(sid, [])),
            }
            for sid in sorted(self._sources)
        ]

    def namespaces(self) -> dict[str, str]:
        """namespace -> owning source_id."""
        return dict(self._namespaces)

    def owned_ids(self, source_id: str) -> list[str]:
        return sorted(self._owned.get(source_id, []))

    def owner_of(self, capability_id: str) -> str | None:
        return self._owner.get(capability_id)

    def executors_by_kind(self) -> dict:
        """{ToolKind -> executor} for every mounted source (execution composition)."""
        return {src.tool_kind: src.build_executor() for src in self._sources.values()}

    # -- Mount / unmount -----------------------------------------------------

    def mount_preloaded(self, source: CapabilitySource) -> list[ToolSpec]:
        """Mount using the source's *already-known* specs (sync, no discovery).

        Used by the (synchronous) runtime factory: the composition root does any
        async discovery on the source first; here we only register what exists.
        """
        return self._mount(source, source.snapshot())

    async def mount(self, source: CapabilitySource) -> list[ToolSpec]:
        """Mount a source, awaiting its ``load()`` (may run discovery)."""
        specs = await source.load()
        return self._mount(source, specs)

    def _mount(self, source: CapabilitySource, specs: list[ToolSpec]) -> list[ToolSpec]:
        if source.source_id in self._sources:
            raise DuplicateSourceError(f"source already mounted: {source.source_id!r}")
        claimed = self._namespaces.get(source.namespace)
        if claimed is not None and claimed != source.source_id:
            raise DuplicateSourceError(
                f"namespace {source.namespace!r} already owned by {claimed!r}"
            )

        validated = self._validate(source, specs, replacing=set())

        for spec in validated:
            self._registry.register(spec)
            self._owner[spec.id] = source.source_id
        self._sources[source.source_id] = source
        self._owned[source.source_id] = [s.id for s in validated]
        self._namespaces[source.namespace] = source.source_id
        return validated

    async def unmount(self, source_id: str) -> None:
        source = self._require(source_id)
        self._remove_owned(source_id)
        self._sources.pop(source_id, None)
        self._namespaces.pop(source.namespace, None)
        await source.close()

    # -- Refresh -------------------------------------------------------------

    async def refresh(self, source_id: str) -> list[ToolSpec]:
        """Atomically refresh one source.

        The new specs are produced and fully validated *before* any registry
        mutation. If discovery or validation fails, the previously registered
        capabilities remain active (no partial/corrupt state).
        """
        source = self._require(source_id)
        specs = await source.reload()  # may raise → nothing mutated yet
        current = set(self._owned.get(source_id, []))
        validated = self._validate(source, specs, replacing=current)

        # Commit: swap old for new only now that the batch is proven valid.
        self._remove_owned(source_id)
        for spec in validated:
            self._registry.register(spec)
            self._owner[spec.id] = source_id
        self._owned[source_id] = [s.id for s in validated]
        return validated

    async def refresh_all(self) -> dict[str, list[ToolSpec]]:
        """Refresh every mounted source. Independent: one failure never rolls back
        or corrupts the others (each refresh is individually atomic)."""
        out: dict[str, list[ToolSpec]] = {}
        for source_id in sorted(self._sources):
            out[source_id] = await self.refresh(source_id)
        return out

    async def shutdown(self) -> None:
        """Close every source (best-effort). Registry contents are left intact."""
        for source in list(self._sources.values()):
            try:
                await source.close()
            except Exception:  # noqa: BLE001 - shutdown must not raise
                pass

    # -- Internals -----------------------------------------------------------

    def _require(self, source_id: str) -> CapabilitySource:
        source = self._sources.get(source_id)
        if source is None:
            raise UnknownSourceError(f"source not mounted: {source_id!r}")
        return source

    def _validate(
        self, source: CapabilitySource, specs: list[ToolSpec], *, replacing: set[str]
    ) -> list[ToolSpec]:
        """Validate a batch before any mutation. ``replacing`` is the set of ids
        this source currently owns (allowed to reappear on refresh)."""
        seen: set[str] = set()
        prefix = source.namespace + "."
        for spec in specs:
            if spec.id in seen:
                raise CapabilityCollisionError(
                    f"duplicate id within source {source.source_id!r}: {spec.id}"
                )
            seen.add(spec.id)

            if source.strict_namespace and not spec.id.startswith(prefix):
                raise NamespaceViolationError(
                    f"source {source.source_id!r} (namespace {source.namespace!r}) "
                    f"emitted out-of-namespace id: {spec.id}"
                )

            owner = self._owner.get(spec.id)
            if owner is not None and owner != source.source_id:
                raise CapabilityCollisionError(
                    f"id {spec.id!r} already owned by source {owner!r}"
                )
            # An id already in the registry but owned by nobody-tracked would be a
            # foreign/pre-existing entry — reject unless this source is replacing it.
            if owner is None and self._registry.exists(spec.id) and spec.id not in replacing:
                raise CapabilityCollisionError(
                    f"id {spec.id!r} already present in the registry (foreign)"
                )
        return list(specs)

    def _remove_owned(self, source_id: str) -> None:
        for tool_id in self._owned.get(source_id, []):
            self._registry.unregister(tool_id)
            self._owner.pop(tool_id, None)
        self._owned[source_id] = []
