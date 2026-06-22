"""Provenance audit command.

Runs the §1/M6 grounding audit (`app.core.audit.audit_provenance`) over one
claim or every claim of a subject, and prints a per-claim OK/issue report.
Read-only — it never writes.

Usage:
    python -m app.commands.audit --claim-id <uuid>
    python -m app.commands.audit --subject-id <uuid>
    python -m app.commands.audit --all

Exit code is non-zero if any audited claim has issues, so it doubles as a
CI / cron data-quality gate.
"""
import argparse
import sys
import uuid
from collections.abc import Sequence

from sqlalchemy import select

from app.core.audit import audit_provenance
from app.db.models import Claim
from app.db.session import session_scope


def _claim_ids(db, args: argparse.Namespace) -> list[uuid.UUID]:
    if args.claim_id:
        return [uuid.UUID(args.claim_id)]
    stmt = select(Claim.id)
    if args.subject_id:
        stmt = stmt.where(Claim.subject_id == uuid.UUID(args.subject_id))
    return list(db.execute(stmt).scalars().all())


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="audit", description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--claim-id", help="Audit a single claim by id.")
    target.add_argument("--subject-id", help="Audit every claim of this subject.")
    target.add_argument(
        "--all", action="store_true", help="Audit every claim in the database."
    )
    args = parser.parse_args(argv)

    failures = 0
    with session_scope() as db:
        ids = _claim_ids(db, args)
        if not ids:
            print("no claims matched", file=sys.stderr)
            return 0
        for claim_id in ids:
            result = audit_provenance(db, claim_id=claim_id)
            print(result.summary)
            if not result.ok:
                failures += 1

    print(f"\n{len(ids)} audited, {failures} with issues")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
