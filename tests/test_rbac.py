import pytest

from core.rbac import (ROLE_PERMISSIONS, AuthError, Session, User,
                       hash_password)
from data.db import Database


@pytest.fixture()
def db(tmp_path):
    d = Database(tmp_path / "t.db")
    yield d
    Session.logout()
    d.close()


def test_admin_can_everything():
    u = User("1", "boss", "admin")
    for perm in {p for perms in ROLE_PERMISSIONS.values() for p in perms}:
        assert u.can(perm)


def test_operator_restrictions():
    u = User("1", "op", "operator")
    assert u.can("scan.run") and u.can("batch.view_own")
    assert not u.can("project.edit")
    assert not u.can("user.manage")
    assert not u.can("batch.view_all")


def test_qc_officer_restrictions():
    u = User("1", "qc", "qc_officer")
    assert u.can("qc.review")
    assert not u.can("scan.run")
    assert not u.can("project.edit")


def test_viewer_read_only():
    u = User("1", "v", "viewer")
    assert u.can("report.view") and u.can("report.export")
    assert not u.can("scan.run") and not u.can("qc.review")


def test_login_seeds_and_authenticates_default_admin(db):
    user = Session.login("admin", "admin", db)
    assert user.role == "admin"
    assert Session.current_user() == user
    # login was audited
    row = db.conn.execute(
        "SELECT * FROM audit_log WHERE action='login'").fetchone()
    assert row["user"] == "admin"


def test_bad_password_raises(db):
    with pytest.raises(AuthError):
        Session.login("admin", "wrong", db)


def test_inactive_user_rejected(db):
    db.insert_user("u9", "gone", hash_password("pw"), "operator")
    db.conn.execute("UPDATE users SET active=0 WHERE username='gone'")
    db.conn.commit()
    with pytest.raises(AuthError):
        Session.login("gone", "pw", db)
