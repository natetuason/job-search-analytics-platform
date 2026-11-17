"""Structured JSON logging with redaction of personal data and secrets."""

import json
import logging
import re
import sys
from datetime import UTC, datetime

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"(?<!\d)(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)")
# user:password@ in connection strings
_URL_CREDS = re.compile(r"(://[^:/@\s]+:)[^@\s]+@")
_SECRET_KV = re.compile(
    r"(?i)\b(password|passwd|secret|token|api[_-]?key|app[_-]?key|app[_-]?id|authorization)\b(\s*[=:]\s*)([^\s&]+)"
)


def redact(text: str) -> str:
    text = _URL_CREDS.sub(r"\1***@", text)
    text = _SECRET_KV.sub(r"\1\2***", text)
    text = _EMAIL.sub("[email]", text)
    return _PHONE.sub("[phone]", text)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": redact(record.getMessage()),
        }
        extra = getattr(record, "fields", None)
        if isinstance(extra, dict):
            payload["fields"] = {k: redact(str(v)) for k, v in extra.items()}
        if record.exc_info:
            payload["exc"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
    # httpx logs every request at INFO; our client logs retries and failures itself.
    logging.getLogger("httpx").setLevel(logging.WARNING)
