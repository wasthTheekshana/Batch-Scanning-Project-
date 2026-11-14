"""Phase 2 security tests: bcrypt auth, DPAPI keystore, SQLCipher DB."""
import sqlite3

import pytest

from security.rbac import (AuthError, Session, User, change_password,
                       create_user, hash_password, is_legacy_hash,
                       reset_password, set_user_active, set_user_role,
                       verify_password, _legacy_hash)
from data.db import Database, is_encrypted, migrate_to_encrypted
from data.keystore import KeyStore


@pytest.fixture()
def db(tmp_path):
    d = Database(tmp_path / "s.db")
    yield d
    Session.logout()
    d.close()


ADMIN = User("a1", "root", "admin")
OPERATOR = User("o1", "op", "operator")


# ---- bcrypt -------------------------------------------------------------------

def test_bcrypt_round_trip():
    h = hash_password("s3cret")
    assert h.startswith("$2")
    assert verify_password("s3cret", h)
    assert not verify_password("wrong", h)


def test_legacy_hash_still_verifies_and_upgrades_on_login(db):
    db.insert_user("u1", "olduser", _legacy_hash("pw123"), "operator")
    assert is_legacy_hash(db.get_user("olduser")["password_hash"])
    user = Session.login("olduser", "pw123", db)
    assert user.role == "operator"
    new_hash = db.get_user("olduser")["password_hash"]
    assert not is_legacy_hash(new_hash)          # upgraded to bcrypt
    Session.logout()
    assert Session.login("olduser", "pw123", db)  # still works after upgrade


# ---- user management ------------------------------------------------------------

def test_create_user_requires_permission(db):
    with pytest.raises(AuthError):
        create_user(db, OPERATOR, "x", "password1", "viewer")
    create_user(db, ADMIN, "alice", "password1", "qc_officer")
    assert db.get_user("alice")["role"] == "qc_officer"


def test_role_and_active_management(db):
    create_user(db, ADMIN, "bob", "password1", "viewer")
    set_user_role(db, ADMIN, "bob", "operator")
    assert db.get_user("bob")["role"] == "operator"
    set_user_active(db, ADMIN, "bob", False)
    with pytest.raises(AuthError):
        Session.login("bob", "password1", db)
    with pytest.raises(ValueError):
        set_user_role(db, ADMIN, "bob", "super_admin")


def test_password_reset_and_change(db):
    create_user(db, ADMIN, "carol", "oldpass12", "viewer")
    reset_password(db, ADMIN, "carol", "newpass12")
    assert Session.login("carol", "newpass12", db)
    Session.logout()
    change_password(db, "carol", "newpass12", "newerpass1")
    assert Session.login("carol", "newerpass1", db)
    Session.logout()
    with pytest.raises(AuthError):
        change_password(db, "carol", "wrong-old", "whatever12")


# ---- keystore --------------------------------------------------------------------

def test_keystore_create_and_reload(tmp_path):
    ks = KeyStore(tmp_path / "keys" / "master.bin")
    key1 = ks.get_or_create()
    key2 = KeyStore(tmp_path / "keys" / "master.bin").get_or_create()
    assert key1 == key2 and len(key1) == 32


def test_keystore_file_is_not_raw_key(tmp_path):
    ks = KeyStore(tmp_path / "master.bin")
    key = ks.get_or_create()
    on_disk = ks.path.read_bytes()
    if ks.is_protected():        # DPAPI available (normal on Windows)
        assert key not in on_disk
    else:                        # documented dev fallback
        assert on_disk.startswith(b"PLAIN1\0")


# ---- SQLCipher --------------------------------------------------------------------

def test_encrypted_db_round_trip(tmp_path):
    key = b"k" * 32
    d = Database(tmp_path / "enc.db", key=key)
    assert d.encrypted
    d.insert_project("p1", "Secret Project", {"name": "Secret Project"})
    d.close()

    # raw file must NOT look like plain SQLite
    assert is_encrypted(tmp_path / "enc.db")
    with pytest.raises(sqlite3.DatabaseError):
        plain = sqlite3.connect(str(tmp_path / "enc.db"))
        plain.execute("SELECT * FROM projects").fetchall()

    # correct key reads it back
    d2 = Database(tmp_path / "enc.db", key=key)
    assert d2.get_project("p1")["name"] == "Secret Project"
    d2.close()


def test_wrong_key_rejected(tmp_path):
    Database(tmp_path / "enc.db", key=b"a" * 32).close()
    with pytest.raises(Exception):
        Database(tmp_path / "enc.db", key=b"b" * 32)


def test_migration_from_plaintext(tmp_path):
    plain = Database(tmp_path / "plain.db")
    plain.insert_project("p1", "Legacy", {})
    plain.insert_user("u1", "admin", hash_password("admin"), "admin")
    plain.close()
    assert not is_encrypted(tmp_path / "plain.db")

    key = b"m" * 32
    migrate_to_encrypted(tmp_path / "plain.db", tmp_path / "enc.db", key)
    assert is_encrypted(tmp_path / "enc.db")
    d = Database(tmp_path / "enc.db", key=key)
    assert d.get_project("p1")["name"] == "Legacy"
    assert d.get_user("admin")["role"] == "admin"
    d.close()


# ---- password rules and forced change -----------------------------------------------

def test_weak_passwords_are_refused(db):
    for bad in ("short", "admin", "dave"):
        with pytest.raises(ValueError):
            create_user(db, ADMIN, "dave", bad, "viewer")
    create_user(db, ADMIN, "dave", "long-enough-1", "viewer")
    with pytest.raises(ValueError):
        reset_password(db, ADMIN, "dave", "tiny")
    with pytest.raises(ValueError):
        change_password(db, "dave", "long-enough-1", "admin")
    assert Session.login("dave", "long-enough-1", db)    # nothing was changed


def test_default_admin_password_must_be_changed(db):
    assert Session.login("admin", "admin", db).must_change_password is True
    Session.logout()
    change_password(db, "admin", "admin", "a-better-pass-1")
    user = Session.login("admin", "a-better-pass-1", db)
    assert user.must_change_password is False
