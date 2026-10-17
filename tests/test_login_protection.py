"""Sign-in protection on the desktop app: lockout after repeated wrong passwords, an
administrator's unlock, and the idle lock."""
from datetime import datetime, timedelta, timezone

import pytest
from PyQt6.QtWidgets import QDialog

from core.rbac import (LOCK_SECONDS, MAX_FAILED_LOGINS, AccountLocked, AuthError, Session,
                       User, create_user, is_locked, unlock_user, verify_session_password)
from data.db import Database
from ui.idle_lock import IdleGuard, UnlockDialog

ADMIN = User("u1", "admin", "admin")


@pytest.fixture(autouse=True)
def cheap_bcrypt(monkeypatch):
    """Cost 12 makes every sign-in take a quarter of a second; the logic under test does not care."""
    import bcrypt
    real = bcrypt.gensalt
    monkeypatch.setattr("core.rbac._bcrypt.gensalt", lambda rounds=12, prefix=b"2b": real(4, prefix))


@pytest.fixture()
def db(tmp_path):
    d = Database(tmp_path / "login.db")
    Session.seed_default_admin(d)
    create_user(d, ADMIN, "clerk", "correct-horse-1", "operator")
    yield d
    Session.logout()
    d.close()


def wrong(db, user="clerk", times=1):
    for _ in range(times):
        with pytest.raises(AuthError):
            Session.login(user, "not-the-password", db)


def test_wrong_passwords_are_counted_and_a_right_one_resets_the_count(db):
    wrong(db, times=MAX_FAILED_LOGINS - 1)
    assert db.get_user("clerk")["failed_logins"] == MAX_FAILED_LOGINS - 1
    Session.login("clerk", "correct-horse-1", db)
    assert db.get_user("clerk")["failed_logins"] == 0


def test_the_fifth_wrong_password_locks_the_account_even_for_the_right_one(db):
    wrong(db, times=MAX_FAILED_LOGINS - 1)
    with pytest.raises(AccountLocked) as caught:
        Session.login("clerk", "not-the-password", db)
    assert caught.value.minutes == LOCK_SECONDS // 60
    with pytest.raises(AccountLocked):
        Session.login("clerk", "correct-horse-1", db)              # locked: even the real password
    assert is_locked(db, "clerk") and not is_locked(db, "admin")   # nobody else is affected
    actions = [r["action"] for r in db.conn.execute("SELECT action FROM audit_log")]
    assert actions.count("login_failed") == MAX_FAILED_LOGINS and "account_locked" in actions
    assert "login_blocked" in actions


def test_the_lock_ends_by_itself(db):
    wrong(db, times=MAX_FAILED_LOGINS)
    past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    db.conn.execute("UPDATE users SET locked_until=? WHERE username='clerk'", (past,))
    db.conn.commit()
    assert Session.login("clerk", "correct-horse-1", db).username == "clerk"


def test_an_administrator_can_unlock_but_an_operator_cannot(db):
    wrong(db, times=MAX_FAILED_LOGINS)
    with pytest.raises(AuthError, match="permission denied"):
        unlock_user(db, User("u3", "clerk", "operator"), "clerk")
    unlock_user(db, ADMIN, "clerk")
    assert not is_locked(db, "clerk")
    assert Session.login("clerk", "correct-horse-1", db).username == "clerk"
    assert "account_unlocked" in [r["action"] for r in db.conn.execute("SELECT action FROM audit_log")]


def test_an_unknown_user_is_refused_without_creating_any_record(db):
    with pytest.raises(AuthError):
        Session.login("nobody", "x", db)
    assert db.get_user("nobody") is None


def test_the_admin_users_table_shows_a_locked_account_and_has_an_unlock_button(qtbot, db):
    from ui.admin_panel import UsersTab
    wrong(db, times=MAX_FAILED_LOGINS)
    tab = UsersTab(db, ADMIN)
    qtbot.addWidget(tab)
    rows = {tab.table.item(r, 0).text(): tab.table.item(r, 2).text() for r in range(tab.table.rowCount())}
    assert "LOCKED" in rows["clerk"] and "LOCKED" not in rows["admin"]
    tab.table.selectRow([r for r in range(tab.table.rowCount()) if tab.table.item(r, 0).text() == "clerk"][0])
    tab.unlock_btn.click()
    assert not is_locked(db, "clerk")


# -- idle lock ------------------------------------------------------------------------------------
def test_the_idle_guard_fires_once_after_the_time_and_input_postpones_it(qtbot):
    guard = IdleGuard(0.01)                      # 0.6 seconds
    hits = []
    guard.idle.connect(lambda: hits.append(1))
    guard._timer.start(150)
    qtbot.wait(60)
    guard.rearm()                                # input: the clock restarts
    guard._timer.start(150)
    qtbot.wait(60)
    assert not hits
    qtbot.waitUntil(lambda: bool(hits), timeout=2000)
    qtbot.wait(300)
    assert hits == [1]


def test_zero_minutes_turns_the_idle_lock_off(qtbot):
    guard = IdleGuard(0)
    guard.rearm()
    assert not guard._timer.isActive()


def test_unlock_needs_the_right_password_and_three_wrong_ones_give_up(qtbot, db):
    dialog = UnlockDialog(db, "clerk")
    qtbot.addWidget(dialog)
    dialog.password.setText("nope")
    dialog.try_unlock()
    assert dialog.result() != QDialog.DialogCode.Accepted and "2 tries left" in dialog.message.text()
    dialog.password.setText("correct-horse-1")
    dialog.try_unlock()
    assert dialog.result() == QDialog.DialogCode.Accepted
    again = UnlockDialog(db, "clerk")
    qtbot.addWidget(again)
    for _ in range(3):
        again.password.setText("nope")
        again.try_unlock()
    assert again.result() == QDialog.DialogCode.Rejected


def test_wrong_unlock_attempts_count_towards_the_account_lock(db):
    for _ in range(MAX_FAILED_LOGINS):
        assert not verify_session_password(db, "clerk", "bad")
    assert is_locked(db, "clerk")
    assert not verify_session_password(db, "clerk", "correct-horse-1")      # locked: refused


def test_the_main_window_locks_and_signs_out_after_a_failed_unlock(qtbot, tmp_path, monkeypatch):
    from core.event_bus import EventBus
    from core.profile_manager import ProfileManager
    from ui.main_window import MainWindow
    d = Database(tmp_path / "w.db")
    Session.login("admin", "admin", d)
    win = MainWindow(d, ProfileManager(tmp_path / "projects", d), EventBus(), staging_dir=tmp_path / "s")
    qtbot.addWidget(win)
    closed = []
    monkeypatch.setattr(win, "sign_out", lambda: closed.append(True))
    monkeypatch.setattr(UnlockDialog, "exec", lambda self: QDialog.DialogCode.Rejected)
    win.lock_session()
    assert closed == [True]
    assert "session_locked" in [r["action"] for r in d.conn.execute("SELECT action FROM audit_log")]
    closed.clear()
    monkeypatch.setattr(UnlockDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    win.lock_session()
    assert closed == [] and "session_unlocked" in [r["action"] for r in d.conn.execute("SELECT action FROM audit_log")]
    Session.logout()
    d.close()
