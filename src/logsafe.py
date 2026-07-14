"""Secret redaction for the logging pipeline (T8, §12).

Credentials must never reach logs. Call sites already mask deliberately
(daily_auth prints a masked token), but defense in depth: a logging.Filter
that scrubs every record — including tracebacks a _safe wrapper logs and
any third-party library line — before it reaches a handler.

What is scrubbed:
- the exact values of fyers.secret_key and fyers.totp_secret (substring
  match; both are long, so no false positives),
- fyers.pin as a standalone word — over-redaction trade-off, stated
  plainly: a quantity or price whose digits exactly equal the PIN will also
  render as [REDACTED]; hiding a static credential wins over that rare
  cosmetic loss,
- anything shaped like a JWT (three dot-joined base64url segments, the form
  of every Fyers access token) — value-independent, so it also catches
  tomorrow's token, which no static list could.

Install once per process entrypoint AFTER logging is configured:
scheduler main() and the API module both do. Filters are attached to
handlers (records from child loggers pass through root's handlers; a
root-level filter would not see them).
"""

from __future__ import annotations

import logging
import re

_JWT = re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")
_MARK = "[REDACTED]"


class SecretRedactor(logging.Filter):
    def __init__(self, secrets: list[str], word_secrets: list[str] = ()):  # noqa: B006
        super().__init__()
        self._subs = [s for s in secrets if s and len(s) >= 6]
        self._words = [re.compile(rf"\b{re.escape(s)}\b")
                       for s in word_secrets if s and len(s) >= 4]

    def scrub(self, text: str) -> str:
        for s in self._subs:
            text = text.replace(s, _MARK)
        for rx in self._words:
            text = rx.sub(_MARK, text)
        return _JWT.sub(_MARK, text)

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = self.scrub(record.getMessage())
            record.args = ()
            # Formatters populate exc_text AFTER filters run, so format the
            # traceback here, scrub it, and cache it — Formatter.format()
            # reuses a non-empty exc_text instead of re-rendering exc_info.
            if record.exc_info and not record.exc_text:
                record.exc_text = logging.Formatter().formatException(record.exc_info)
            if record.exc_text:
                record.exc_text = self.scrub(record.exc_text)
            if record.stack_info:
                record.stack_info = self.scrub(record.stack_info)
        except Exception:
            pass  # a redaction bug must never suppress the log line itself
        return True


def install_redaction(config) -> SecretRedactor:
    """Attach the redactor to every current root handler (idempotent)."""
    redactor = SecretRedactor(
        secrets=[config["fyers.secret_key"], config["fyers.totp_secret"]],
        word_secrets=[config["fyers.pin"]])
    root = logging.getLogger()
    for h in root.handlers or [logging.lastResort]:
        if not any(isinstance(f, SecretRedactor) for f in h.filters):
            h.addFilter(redactor)
    return redactor
