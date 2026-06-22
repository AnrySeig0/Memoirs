"""Logging setup — PII redaction filter for compliance-safe logs.

`setup_logging()` is called once from the app entry point (Phase 4). The
redaction filter scrubs emails / tokens / secrets from every record so they
never reach a log aggregator.
"""
import logging
import re
from typing import ClassVar


class PiiRedactionFilter(logging.Filter):
    """Logging filter that redacts personally identifiable information."""

    PATTERNS: ClassVar[list[tuple[re.Pattern[str], str]]] = [
        # Email addresses
        (re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"), "[EMAIL_REDACTED]"),
        # JWT tokens (header.payload.signature)
        (
            re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+"),
            "[JWT_REDACTED]",
        ),
        # Generic long secrets following a token/key/secret/password label
        (
            re.compile(
                r"(?:token|key|secret|password|authorization)[=: ]+['\"]?([A-Za-z0-9_/+=.-]{20,})",
                re.IGNORECASE,
            ),
            "[SECRET_REDACTED]",
        ),
        # Bearer tokens
        (re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]{10,}"), "Bearer [TOKEN_REDACTED]"),
    ]

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = self._redact(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    k: self._redact(v) if isinstance(v, str) else v
                    for k, v in record.args.items()
                }
            elif isinstance(record.args, tuple):
                record.args = tuple(
                    self._redact(a) if isinstance(a, str) else a for a in record.args
                )
        return True

    def _redact(self, value: str) -> str:
        for pattern, replacement in self.PATTERNS:
            value = pattern.sub(replacement, value)
        return value


def setup_logging(level: int = logging.INFO) -> None:
    """Configure the root logger with a basic format + PII redaction."""
    logging.basicConfig(
        level=level,
        format="%(levelname)-5.5s [%(name)s] %(message)s",
    )
    root_logger = logging.getLogger()
    if not any(isinstance(f, PiiRedactionFilter) for f in root_logger.filters):
        root_logger.addFilter(PiiRedactionFilter())
