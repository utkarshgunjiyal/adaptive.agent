"""GitHub resource resolver (Phase 46.3.1; cross-turn memory in 46.3.2).

The GitHub implementation of the provider-agnostic ``ResourceResolver``. It reuses
the existing deterministic parsing (``github/resources.py``) and trusted identity
(``github/identity.py``) — no duplicated parsing — and emits provider-neutral
``Resource`` objects tagged with their deterministic source.

Resolution priority (deterministic; no LLM):
1. the current request (explicit owner/repo, issue/PR number, "my"),
2/3. the thread's remembered resources (prior successful outputs / thread state),
4. the trusted connector identity ("my" → the authenticated owner),
5. cached context.

Explicit resources in the current request always override remembered ones. A bare
repository name or account scope is never overridden by memory; memory only fills a
repository/owner the request left completely unspecified.
"""

from __future__ import annotations

from app.agent.github.identity import GithubIdentity
from app.agent.github.resources import resolve_resources
from app.agent.resources.models import (
    ResolutionContext,
    Resource,
    ResolvedResources,
    ResourceSource,
)

GITHUB_PROVIDER = "github"

# GithubResources.owner_source → deterministic ResourceSource.
_OWNER_SOURCE = {
    "explicit": ResourceSource.REQUEST,
    "connector_identity": ResourceSource.CONNECTOR_IDENTITY,
    "prior_context": ResourceSource.THREAD_STATE,
}


class GithubResourceResolver:
    """Resolves GitHub resources (owner/repo/issue_number/pull_number + scope)."""

    provider = GITHUB_PROVIDER

    def __init__(self, *, identity: GithubIdentity | None = None) -> None:
        self._identity = identity or GithubIdentity()

    def resolve(self, ctx: ResolutionContext) -> ResolvedResources:
        # Explicit ambiguity candidates (legacy/test hook): a bare repo name that
        # maps to several owners → clarify, never guess.
        known = ctx.execution_state.get("github_active_repositories")
        known = known if isinstance(known, list) else None
        # Thread-remembered resources (Phase 46.3.2), strictly thread/user-scoped.
        remembered = ctx.execution_state.get("remembered")
        remembered = remembered if isinstance(remembered, dict) else {}

        req = resolve_resources(ctx.user_request, identity=self._identity, known_repositories=known)

        resources: dict[str, Resource] = {}
        ambiguous = {"owner": req.owner_candidates} if req.owner_candidates > 1 else {}
        flags = {"account_scoped": bool(req.account_scoped)}

        # Numbers come from the current request only (never a stale remembered id).
        if req.issue_number is not None:
            resources["issue_number"] = Resource(type="issue_number", value=req.issue_number,
                                                 source=ResourceSource.REQUEST, provider=self.provider)
        if req.pull_number is not None:
            resources["pull_number"] = Resource(type="pull_number", value=req.pull_number,
                                                source=ResourceSource.REQUEST, provider=self.provider)

        owner = req.owner
        owner_source = _OWNER_SOURCE.get(req.owner_source) if req.owner else None
        repo = req.repo
        repo_source = ResourceSource.REQUEST if req.repo else None

        # Cross-turn fill: ONLY when the request specified neither an explicit
        # repository nor account scope ("my"), and there is no ambiguity. This is
        # the "List open issues." case after a repo was established this thread;
        # it never overrides an explicit repo or an account-wide listing.
        if req.repo is None and not req.account_scoped and not ambiguous:
            if remembered.get("repo"):
                repo = remembered["repo"]
                repo_source = ResourceSource.PRIOR_OUTPUT
            if owner is None and remembered.get("owner"):
                owner = remembered["owner"]
                owner_source = ResourceSource.PRIOR_OUTPUT

        if owner:
            resources["owner"] = Resource(type="owner", value=owner,
                                          source=owner_source or ResourceSource.REQUEST,
                                          provider=self.provider)
        if repo:
            resources["repo"] = Resource(type="repo", value=repo,
                                         source=repo_source or ResourceSource.REQUEST,
                                         provider=self.provider)

        return ResolvedResources(
            provider=self.provider, resources=resources, ambiguous=ambiguous, flags=flags,
        )
