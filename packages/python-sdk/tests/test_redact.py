"""`redact_secrets` birim testleri (B-055).

Protection runs both ways: secrets MUST go, and nothing the agent actually needs may go with
them. Over-redaction is a bug too — dropping `otp_code` would quietly make the tools useless.

Connected to:
  - exercises: mailflat.redact

Running it: cd packages/python-sdk && python -m pytest tests/test_redact.py
"""
from mailflat import redact_secrets
from mailflat.redact import SECRET_NAME_PARTS


def test_strips_api_key_at_top_level():
    out = redact_secrets({"ok": True, "address": "a@x.net", "api_key": "mf_sk_secret"})
    assert out == {"ok": True, "address": "a@x.net"}


def test_strips_inside_lists_and_nested_dicts():
    payload = {
        "ok": True,
        "inboxes": [
            {"address": "a@x.net", "api_key": "mf_sk_1"},
            {"address": "b@x.net", "api_key": "mf_sk_2", "meta": {"apiKey": "mf_sk_3"}},
        ],
    }
    out = redact_secrets(payload)
    assert "mf_sk_" not in str(out)
    assert [i["address"] for i in out["inboxes"]] == ["a@x.net", "b@x.net"]
    assert out["inboxes"][1]["meta"] == {}


def test_name_matching_is_shape_insensitive():
    """`api_key` · `apiKey` · `API-KEY` — all the same field."""
    for name in ("api_key", "apiKey", "APIKEY", "API-KEY", "inbox_api_key"):
        assert redact_secrets({name: "x", "keep": 1}) == {"keep": 1}, name


def test_secret_and_password_fields_go_too():
    out = redact_secrets({"webhook_secret": "s", "password": "p", "address": "a@x.net"})
    assert out == {"address": "a@x.net"}
    assert set(SECRET_NAME_PARTS) == {"apikey", "secret", "password"}


def test_keeps_what_the_agent_needs():
    """🔒 Over-redaction is a bug too: OTP and message fields MUST survive."""
    msg = {
        "id": 7, "subject": "Verify", "body_text": "your code is 123456",
        "otp_code": "123456", "sender": "no-reply@stripe.com", "received_at": "2026-08-04T10:00:00",
        "to_address": "a@x.net", "is_read": False, "direction": "in",
    }
    assert redact_secrets(msg) == msg


def test_token_is_deliberately_not_redacted():
    """`token` is NOT in the list — fields like a domain verify_token are harmless and useful."""
    assert redact_secrets({"verify_token": "mf-verify-abc"}) == {"verify_token": "mf-verify-abc"}


def test_does_not_scan_values_only_names():
    """No value-based scanning: an email that merely discusses a key must stay intact."""
    msg = {"subject": "Your key", "body_text": "we rotated mf_sk_oldkey for you"}
    assert redact_secrets(msg) == msg


def test_input_is_not_mutated():
    original = {"api_key": "mf_sk_1", "address": "a@x.net"}
    redact_secrets(original)
    assert original["api_key"] == "mf_sk_1", "the input must not be mutated (the SDK reuses it)"


def test_passes_scalars_and_none_through():
    for value in ("plain", 7, 1.5, True, None):
        assert redact_secrets(value) == value
    assert redact_secrets([]) == []
    assert redact_secrets({}) == {}


def test_tuples_become_lists():
    """JSON carries a list anyway; preserving tuples would be fake fidelity."""
    assert redact_secrets(({"api_key": "x", "a": 1},)) == [{"a": 1}]


# ========================================== value-based redaction (round 7 #5)
def test_a_key_in_an_unexpected_field_is_still_redacted():
    """A name-based defence cannot see a field name nobody thought of.

    An external review showed it: a real `mf_live_` key carried in a field called
    `account` passed through untouched. Enumerating field names leaves every name outside
    the list unprotected, and no such list is ever complete.
    """
    out = redact_secrets({"account": "mf_live_REAL", "free": "mf_sk_REAL",
                          "nested": {"whatever": "mf_live_REAL"}})
    assert "mf_live_REAL" not in str(out)
    assert "mf_sk_REAL" not in str(out)
    assert "[redacted]" in str(out), "must be visibly masked, not silently dropped"


def test_a_key_inside_an_error_sentence_is_redacted():
    """Error sentences reach the model too; a key landing there is the same leak."""
    out = redact_secrets({"error": "Auth failed for mf_live_ABC123"})
    assert "mf_live_ABC123" not in out["error"]


def test_message_content_is_NOT_touched():
    """NEGATIVE CONTROL — and the original reason value scanning was rejected.

    An email may legitimately discuss a test key. Masking it breaks the very message the
    agent is waiting for: a worse failure than the one being prevented. The exception was
    not removed, it was NARROWED to content fields, and this test locks that narrowing.
    """
    body = "Your API key is mf_sk_example123 — paste it into the form."
    for field in ("body", "body_text", "body_html", "html", "text"):
        out = redact_secrets({field: body})
        assert out[field] == body, f"'{field}' content was mangled — real mail would suffer"
