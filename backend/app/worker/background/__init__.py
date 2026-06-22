"""Background tasks (sync).

The Review API is synchronous (sync SQLAlchemy session), so there is no
event loop to offload to. These functions are written to run in a separate
worker process/thread the way a Celery/Prefect task would: each opens its
own short-lived DB session via `app.db.session.session_scope` and owns its
transaction. No distributed queue is wired up yet — Redis is configured
(§5 "Orchestration") but unused until V1+. Until then a caller can invoke
these directly (e.g. from a CLI command or a thread).
"""
