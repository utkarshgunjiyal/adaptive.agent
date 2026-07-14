# Test Credentials

Runner.ai uses per-test dynamic user creation. The pytest suites in
`/app/backend/tests/` register a fresh user on each session, so no
long-lived credential is required.

## Manual smoke test users (Phase 1)

You can create a workspace directly at
`https://<preview-url>/register` with any email/password (min 6 chars).
Every user has isolated threads, documents, and adaptive runs.

## Provider secrets

- LLM credentials — set in `/app/backend/.env`. Runner.ai uses user-owned
  credentials: `OPENROUTER_API_KEY` (default provider, OpenAI-compatible) or
  `ANTHROPIC_API_KEY` (direct Anthropic). Both the legacy synthesizer and the
  adaptive runtime go through the same provider factory. With no key set,
  `LLM_PROVIDER` resolves to `stub` (deterministic, no network) so tests that
  don't require a live model still run.
- `TAVILY_API_KEY` — set in `/app/backend/.env`. Optional; when unset
  the `web_search` tool is registered but reports `unavailable`.

## Frontend E2E (Playwright)

Frontend test creates a new user each run:
- email: `fe_v2_<hex>@example.com`
- password: `TestPass123!`
- name: `Phase1 Verify`
