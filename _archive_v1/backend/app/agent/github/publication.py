"""GitHub post-success resource extraction (Phase 46.3.2.1).

After a successful GitHub call, derive the resources worth remembering from the
TRUSTED normalized output (never raw text, never the token). The key case is
``search_repositories``: publish a COMPLETE active-repository identity
(owner + repo) only when the result unambiguously identifies one repository —

- a lookup returning exactly one repository → publish its owner + repo;
- an explicit repository name in the request confirmed among the results →
  publish that owner + repo.

A broad account listing that returns many repositories publishes **no** active
repository (never pick one arbitrarily). Repo-scoped tools (list_issues,
issue_read, list_pull_requests, pull_request_read) already carry owner/repo as
required resolved inputs, so their active-repo memory comes from the resolved
resources (this extractor adds nothing for them).
"""

from __future__ import annotations

from app.agent.github.identity import validate_owner
from app.agent.models.tool_spec import ToolSpec
from app.agent.resources.models import Resource, ResourceSource

GITHUB_PROVIDER = "github"


def _tool_name(tool: ToolSpec) -> str | None:
    ref = getattr(tool, "handler_ref", None)
    if isinstance(ref, str) and ref.startswith("mcp:"):
        parts = ref.split(":", 2)
        if len(parts) == 3:
            return parts[2]
    return None


def _select_active(repos: list, requested_repo) -> dict | None:
    """The single active repository, or None when it can't be determined safely."""
    valid = [r for r in repos if isinstance(r, dict)]
    if len(valid) == 1:
        return valid[0]
    if requested_repo:
        matches = [r for r in valid if str(r.get("name", "")).lower() == str(requested_repo).lower()]
        if len(matches) == 1:
            return matches[0]
    return None  # broad/ambiguous listing → never guess an active repo


class GithubResourceExtractor:
    """Extracts a complete active-repository identity from trusted output."""

    provider = GITHUB_PROVIDER

    def extract(self, tool: ToolSpec, resolved: dict, output: dict) -> list[Resource]:
        if _tool_name(tool) != "search_repositories":
            return []
        if not isinstance(output, dict) or output.get("kind") != "repositories":
            return []
        repos = output.get("repositories") or []
        if not isinstance(repos, list):
            return []

        active = _select_active(repos, (resolved or {}).get("repo"))
        if not active:
            return []

        owner = validate_owner(active.get("owner"))
        repo = active.get("name")
        # Publish only a COMPLETE (owner, repo) identity, from trusted output.
        if not (owner and repo):
            return []
        return [
            Resource(type="owner", value=owner, source=ResourceSource.PRIOR_OUTPUT, provider=self.provider),
            Resource(type="repo", value=str(repo), source=ResourceSource.PRIOR_OUTPUT, provider=self.provider),
        ]
