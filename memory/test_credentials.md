# Test Credentials

Runner.ai uses per-test dynamic user creation. The pytest suites in
`/app/backend/tests/` register a fresh user on each session, so no
long-lived credential is required.

## Manual smoke test users (Phase 1)

You can create a workspace directly at
`https://<preview-url>/register` with any email/password (min 6 chars).
Every user has isolated threads, documents, and adaptive runs.

## Provider secrets

- `EMERGENT_LLM_KEY` — set in `/app/backend/.env`. Used by both the legacy
  synthesizer and the adaptive Emergent provider adapter. Universal key
  routes Claude Sonnet 4.5 through LiteLLM under the hood.
- `TAVILY_API_KEY` — set in `/app/backend/.env`. Optional; when unset
  the `web_search` tool is registered but reports `unavailable`.

## Frontend E2E (Playwright)

Frontend test creates a new user each run:
- email: `fe_v2_<hex>@example.com`
- password: `TestPass123!`
- name: `Phase1 Verify`
