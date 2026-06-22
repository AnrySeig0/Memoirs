"""Entry point for `uvicorn main:app`.

The app factory lives in `app.main`. This module is a thin shim so the
existing deployment command (`uvicorn main:app --host 0.0.0.0 --port 8000`)
keeps working alongside the template-style `uvicorn app.main:app`.
"""
from app.main import app

__all__ = ["app"]
