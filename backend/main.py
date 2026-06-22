"""Entry point for `uvicorn main:app`.

The actual app lives in `memoir.api`. This module is kept thin so the
deployment command (`uvicorn main:app --host 0.0.0.0 --port 8000`) stays
stable across milestones.
"""
from app.api import app

__all__ = ["app"]
