"""Runner.ai backend entry point.

Uvicorn is launched by supervisor with ``server:app`` from ``/app/backend``.
All routes are mounted under the ``/api`` prefix so the Kubernetes ingress can
route them to port 8001.
"""

from dotenv import load_dotenv

load_dotenv()

# Import after load_dotenv() so env vars are available for module-level config.
from app.main import app  # noqa: E402

__all__ = ["app"]
