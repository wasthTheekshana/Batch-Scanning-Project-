import pytest

from central.config_rules import (MAX_CONFIG_BYTES, check_config,
                                  find_credential_path)


def test_finds_nested_credential_keys_case_insensitively():
    cfg = {"delivery": {"sftp": {"host": "h", "Password": "x"}}}
    assert find_credential_path(cfg) == "delivery.sftp.Password"


def test_finds_credentials_inside_lists():
    assert find_credential_path({"a": [{"api_key": "k"}]}) == "a[0].api_key"


def test_clean_config_has_no_credential_path():
    assert find_credential_path({"ocr": {"lang": "eng"}, "key_file": "C:/k"}) is None


def test_check_config_rejects_credentials_and_oversize():
    with pytest.raises(ValueError, match="credential"):
        check_config({"sftp": {"password": "x"}})
    with pytest.raises(ValueError, match="too large"):
        check_config({"blob": "x" * (MAX_CONFIG_BYTES + 1)})
    check_config({"ok": True})
