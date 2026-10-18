"""Encryption at rest: what is stored cannot be read without the key, cannot be moved to another place,
and the blind index finds a value without storing it."""
import pytest

from central.crypto import DataKey, DecryptionError, KeyUnavailable, generate_key, load_data_key


def key(**kw):
    return DataKey(generate_key(), **kw)


def test_text_round_trips_and_the_stored_form_hides_it():
    k = key()
    stored = k.encrypt("553216789V", "doc-1")
    assert "553216789V" not in stored and stored.startswith("1:")
    assert k.decrypt_text(stored, "doc-1") == "553216789V"


def test_the_same_text_encrypts_differently_each_time():
    k = key()
    assert k.encrypt("same", "c") != k.encrypt("same", "c")


def test_text_moved_to_another_place_or_altered_does_not_decrypt():
    k = key()
    stored = k.encrypt("secret", "doc-1")
    with pytest.raises(DecryptionError):
        k.decrypt(stored, "doc-2")                          # another row's context
    tampered = stored[:-4] + ("AAAA" if not stored.endswith("AAAA") else "BBBB")
    with pytest.raises(DecryptionError):
        k.decrypt(tampered, "doc-1")
    with pytest.raises(DecryptionError):
        k.decrypt("not even a token", "doc-1")


def test_another_key_cannot_read_it():
    stored = key().encrypt("secret", "c")
    with pytest.raises(DecryptionError):
        key().decrypt(stored, "c")


def test_a_rotated_key_still_reads_old_data_and_writes_with_the_new_version():
    old_master, new_master = generate_key(), generate_key()
    old = DataKey(old_master, version=1)
    stored_old = old.encrypt("kept", "c")
    new = DataKey(new_master, version=2, old={1: old_master})
    assert new.decrypt_text(stored_old, "c") == "kept"
    assert new.encrypt("fresh", "c").startswith("2:")
    with pytest.raises(DecryptionError):
        DataKey(new_master, version=2).decrypt(stored_old, "c")        # without the old key it is unreadable


# -- the blind index ------------------------------------------------------------------------------------------
def test_two_spellings_of_an_identifier_have_one_blind_hash():
    k = key()
    assert k.blind("nic", "553 216-789v") == k.blind("nic", "553216789V") != ""
    assert k.blind("nic", "553216789V") != k.blind("nic", "553216789X")


def test_the_blind_hash_is_scoped_to_the_field_and_the_key():
    k = key()
    assert k.blind("nic", "12345678") != k.blind("account_number", "12345678")
    assert k.blind("nic", "12345678") != key().blind("nic", "12345678")
    assert "553216789" not in k.blind("nic", "553216789V")


def test_nothing_to_look_up_gives_an_empty_hash():
    assert key().blind("nic", " -- ") == "" and key().blind_all("nic", "") == []


def test_a_lookup_during_rotation_tries_every_key_version():
    old_master, new_master = generate_key(), generate_key()
    old = DataKey(old_master, version=1)
    new = DataKey(new_master, version=2, old={1: old_master})
    assert old.blind("nic", "553216789V") in new.blind_all("nic", "553216789V")
    assert len(new.blind_all("nic", "553216789V")) == 2


# -- loading ---------------------------------------------------------------------------------------------------
def test_no_key_configured_is_none_not_an_error():
    assert load_data_key({}) is None


def test_the_key_comes_from_the_environment_or_a_file(tmp_path):
    master = generate_key()
    from_env = load_data_key({"CENTRAL_DATA_KEY": master})
    path = tmp_path / "data.key"
    path.write_text(master + "\n", encoding="utf-8")
    from_file = load_data_key({"CENTRAL_DATA_KEY_FILE": str(path)})
    assert from_file.decrypt_text(from_env.encrypt("x", "c"), "c") == "x"


@pytest.mark.parametrize("bad", ["short", "!!!not base64!!!", "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVo="])   # 26 bytes
def test_a_malformed_key_is_refused_with_a_plain_message(bad):
    with pytest.raises(KeyUnavailable):
        load_data_key({"CENTRAL_DATA_KEY": bad})


def test_a_missing_key_file_is_refused(tmp_path):
    with pytest.raises(KeyUnavailable):
        load_data_key({"CENTRAL_DATA_KEY_FILE": str(tmp_path / "nope.key")})


def test_the_old_key_setting_is_understood():
    old_master, new_master = generate_key(), generate_key()
    stored = DataKey(old_master, version=1).encrypt("kept", "c")
    k = load_data_key({"CENTRAL_DATA_KEY": new_master, "CENTRAL_DATA_KEY_VERSION": "2",
                       "CENTRAL_DATA_KEY_OLD": f"1:{old_master}"})
    assert k.decrypt_text(stored, "c") == "kept" and k.current == 2
