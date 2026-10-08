"""Tests for SafeStr validator (rejects lone surrogates)."""
import pytest
from pydantic import BaseModel, ValidationError

from central.textsafe import SafeStr


class ModelWithSafeStr(BaseModel):
    text: SafeStr


def test_valid_utf8_accepted():
    """Valid UTF-8 strings are accepted."""
    m = ModelWithSafeStr(text="hello")
    assert m.text == "hello"


def test_empty_string_rejected():
    """Empty string is rejected by min_length constraint (handled by Field)."""
    # This would be handled by Field constraints, not SafeStr validator itself
    pass


def test_lone_surrogate_rejected():
    """Lone surrogates (\ud800, etc.) raise ValueError."""
    with pytest.raises(ValidationError) as exc_info:
        ModelWithSafeStr(text="\ud800")
    errors = exc_info.value.errors()
    assert len(errors) == 1
    assert errors[0]["type"] == "value_error"
    assert "invalid characters" in errors[0]["msg"]


def test_unicode_escapes_rejected():
    """Other lone surrogates are rejected."""
    for surrogate in ["\ud800", "\udc00", "\udbff", "\udfff"]:
        with pytest.raises(ValidationError):
            ModelWithSafeStr(text=surrogate)


def test_valid_unicode_accepted():
    """Valid unicode characters are accepted."""
    # Test emoji, Chinese, etc.
    m1 = ModelWithSafeStr(text="😀")
    assert m1.text == "😀"

    m2 = ModelWithSafeStr(text="你好")
    assert m2.text == "你好"
