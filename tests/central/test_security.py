import json

import pytest

from central import security as sec
from central.audit import record
from central.models import AuditEvent


def test_password_roundtrip():
    h = sec.hash_password("pw one")
    assert sec.verify_password("pw one", h)
    assert not sec.verify_password("pw two", h)


def test_long_and_multibyte_passwords_work_and_stay_significant():
    long_pw = "a" * 150
    h = sec.hash_password(long_pw)
    assert sec.verify_password(long_pw, h)
    assert not sec.verify_password("a" * 149 + "b", h)  # tail is not truncated
    assert sec.verify_password("pässwörd-日本語-" * 10, sec.hash_password("pässwörd-日本語-" * 10))
    assert sec.burn_password_check("z" * 200) is False  # must not raise


def test_verify_password_with_garbage_hash_is_false_not_error():
    assert sec.verify_password("x", "not-a-bcrypt-hash") is False


def test_burn_password_check_is_always_false():
    assert sec.burn_password_check("whatever") is False


def test_api_keys_are_unique_and_prefixed():
    a, b = sec.new_api_key(), sec.new_api_key()
    assert a != b and a.startswith("dsk_") and len(a) >= 40


def test_hash_secret_is_deterministic_sha256_hex():
    assert sec.hash_secret("a") == sec.hash_secret("a")
    assert len(sec.hash_secret("a")) == 64
    assert sec.hash_secret("a") != sec.hash_secret("b")


def test_enrollment_codes_are_long_and_unique():
    a, b = sec.new_enrollment_code(), sec.new_enrollment_code()
    assert a != b and len(a) >= 16


def test_sanitize_log_value_strips_control_chars_and_truncates():
    assert sec.sanitize_log_value("op\nFORGED: x") == "op?FORGED: x"
    assert sec.sanitize_log_value("a\r\x00b\x7fc") == "a??b?c"
    assert len(sec.sanitize_log_value("x" * 500)) == 100
    assert sec.sanitize_log_value(None) == ""


def test_audit_record_adds_row_without_committing(app):
    with app.state.session_factory() as db:
        record(db, "admin:root", "pc_created", {"pc_id": "p1"})
        db.rollback()
        assert db.query(AuditEvent).count() == 0
        record(db, "admin:root", "pc_created", {"pc_id": "p1"})
        db.commit()
        row = db.query(AuditEvent).one()
        assert row.actor == "admin:root" and row.action == "pc_created"
        assert json.loads(row.detail) == {"pc_id": "p1"}


def test_lone_surrogates_do_not_crash_or_enumerate():
    """Lone surrogates (from json.loads) must not raise in any path."""
    # burn_password_check must not raise (would return 500 revealing unknown user)
    assert sec.burn_password_check("\ud800") is False
    # hash_password must not raise (would return 500)
    h = sec.hash_password("\ud800")
    # verify_password must work roundtrip
    assert sec.verify_password("\ud800", h) is True
    # Different lone surrogate must not match
    assert sec.verify_password("\ud801", h) is False


def test_hash_secret_with_lone_surrogate_does_not_raise():
    """hash_secret must handle lone surrogates (from json.loads) without raising."""
    result = sec.hash_secret("\ud800")
    assert result is not None
    assert len(result) == 64  # SHA-256 hex is always 64 chars


def test_hash_secret_lone_surrogates_differ():
    """Different lone surrogates must produce different hashes."""
    hash1 = sec.hash_secret("\ud800")
    hash2 = sec.hash_secret("\ud801")
    assert hash1 != hash2


@pytest.mark.real_bcrypt
def test_hash_password_uses_production_cost_factor():
    """The suite makes bcrypt cheap; this pins the real cost (>= 12)."""
    h = sec.hash_password("some password")
    prefix, cost = h.split("$")[1:3]
    assert prefix == "2b" and int(cost) >= 12
    assert sec.verify_password("some password", h)
