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


# Issue 2: Enhanced credential detection with normalization
@pytest.mark.parametrize("key,should_flag", [
    ("password", True),
    ("Password", True),
    ("Pass-Word", True),
    ("PASS_WORD", True),
    ("api_key", True),
    ("API-Key", True),
    ("APIKey", True),
    ("client_secret", True),
    ("access_token", True),
    ("refresh_token", True),
    ("private_key", True),
    ("privateKey", True),
    ("auth_token", True),
    ("passphrase", True),
    ("PASSPHRASE", True),
    ("key_file", False),  # Path, not secret
    ("keyfile", False),   # Path, not secret
    ("confidence_threshold", False),
    ("ocr", False),
    ("rules", False),
])
def test_credential_key_detection_with_variants(key, should_flag):
    """Test that various credential key naming patterns are detected."""
    cfg = {"field": {key: "value"}}
    result = find_credential_path(cfg)
    if should_flag:
        assert result == f"field.{key}", f"Expected to find credential in {key}"
    else:
        assert result is None, f"Should not flag {key} as credential"


def test_credential_url_userinfo_detection():
    """Test that URL userinfo patterns (user:password@host) are flagged."""
    cfg = {"delivery": {"url": "sftp://user:pass@host.com/path"}}
    result = find_credential_path(cfg)
    assert result == "delivery.url"


def test_credential_url_without_userinfo_allowed():
    """Test that URLs without userinfo are allowed."""
    cfg = {"api": {"endpoint": "https://example.com/api/v1"}}
    assert find_credential_path(cfg) is None


def test_credential_pem_key_detection():
    """Test that PEM private key headers are detected."""
    cfg = {"cert": {"key": "-----BEGIN PRIVATE KEY-----\nMIIEvQ..."}}
    result = find_credential_path(cfg)
    assert result == "cert.key"


def test_regression_default_project_config_passes(project_config):
    """Ensure the default project config passes credential check."""
    check_config(project_config)  # Should not raise


# Issue 3: Deep nesting limit
def test_deeply_nested_dict_rejected():
    """Test that dict nested >64 levels is rejected."""
    cfg = {}
    current = cfg
    for i in range(500):
        current["nested"] = {}
        current = current["nested"]
    current["value"] = "x"

    with pytest.raises(ValueError, match="nested too deeply"):
        check_config(cfg)


def test_deeply_nested_list_rejected():
    """Test that list nested >64 levels is rejected."""
    cfg = []
    current = cfg
    for i in range(5000):
        new_list = []
        current.append(new_list)
        current = new_list
    current.append("value")

    with pytest.raises(ValueError, match="nested too deeply"):
        check_config(cfg)


def test_moderately_nested_config_accepted():
    """Test that config nested 60 levels deep is accepted."""
    cfg = {}
    current = cfg
    for i in range(60):
        current["nested"] = {}
        current = current["nested"]
    current["value"] = "x"

    check_config(cfg)  # Should not raise


# M3: scanner gaps
@pytest.mark.parametrize("key", ["pwd", "Pwd", "pass", "PASS", "credential",
                                 "credentials", "Credentials", "authorization",
                                 "Authorization"])
def test_exact_credential_keys_are_flagged(key):
    assert find_credential_path({"db": {key: "v"}}) == f"db.{key}"


@pytest.mark.parametrize("key", ["passthrough", "bypass_mode", "pass_through",
                                 "authorization_mode", "credential_store_path"])
def test_exact_key_match_is_not_a_substring_match(key):
    assert find_credential_path({"db": {key: "v"}}) is None


@pytest.mark.parametrize("value", [
    "Driver={SQL Server};Server=h;Uid=u;Pwd=hunter2;",
    "DSN=x;PASSWORD=abc",
    "https://h/path?token=abc123&page=2",
    "https://h/?sig=AbCdEf",
    "host=h;apikey=zzz",
    "https://h/?x=1&secret=s",
    "Basic dXNlcjpwYXNzd29yZA==",
    "  bearer abcdefghijkl",
    "sftp://:pw@host/path",
])
def test_credential_value_patterns_are_flagged(value):
    assert find_credential_path({"endpoint": value}) == "endpoint"
    assert find_credential_path({"list": [value]}) == "list[0]"


@pytest.mark.parametrize("value", [
    "https://example.com/?page=2",
    "https://example.com:8080/path",
    "Server=h;Database=d;Trusted_Connection=yes",
    "bearer",
    "basic short",
    "sftp://user@host/path",
    "bypass_mode=on",
])
def test_harmless_values_are_not_flagged(value):
    assert find_credential_path({"endpoint": value}) is None
