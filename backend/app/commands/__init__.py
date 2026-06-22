"""CLI commands.

Lightweight, dependency-free (stdlib `argparse`) operational commands. Each
module exposes a `main(argv=None)` entry point and is runnable as
`python -m app.commands.<name>`. They open their own DB session via
`app.db.session.session_scope` — they do not go through the API.
"""
