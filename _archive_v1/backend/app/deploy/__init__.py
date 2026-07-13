"""Deployment-time checks (Phase 42B).

Small, config-free helpers used at startup and by the deploy scripts:

- ``startup_guard`` — refuses an unsafe production boot (silent dev auth, demo
  mode in production).
- ``env_check`` — validates a deployment environment (required vars, placeholder
  secrets, CORS/domain agreement, secure-cookie posture) without importing
  application settings and without ever printing secret values.

Both operate on explicitly-passed values / a plain mapping so they are unit
testable in the config-free sandbox and reusable from a CLI on the VM.
"""
