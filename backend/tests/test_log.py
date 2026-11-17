import json
import logging

from app.log import JsonFormatter, redact


def test_redacts_email_phone_and_secrets() -> None:
    out = redact(
        "user jane.doe@example.com phone (732) 555-0199 "
        "postgresql+psycopg://jobs:hunter2@db:5432/jobs api_key=sk-abc123"
    )
    assert "jane.doe" not in out
    assert "555-0199" not in out
    assert "hunter2" not in out
    assert "sk-abc123" not in out
    assert "[email]" in out and "[phone]" in out


def test_leaves_ordinary_text_alone() -> None:
    assert redact("fetched 42 jobs from greenhouse board acme") == "fetched 42 jobs from greenhouse board acme"


def test_formatter_emits_json_with_redacted_fields() -> None:
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "contact a@b.co", None, None)
    record.fields = {"url": "https://u:pw@host/x"}
    payload = json.loads(JsonFormatter().format(record))
    assert payload["msg"] == "contact [email]"
    assert "pw" not in payload["fields"]["url"]
