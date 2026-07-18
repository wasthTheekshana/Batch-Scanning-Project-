"""Admin panel — user management + backup settings (HLD §16, Phase 2).

All actions go through core.rbac helpers, which enforce user.manage /
config.backup permissions and write audit rows.
"""
from __future__ import annotations

from PyQt6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox,
                             QDoubleSpinBox, QFileDialog, QFormLayout,
                             QHBoxLayout, QInputDialog, QLabel, QLineEdit,
                             QMessageBox, QPushButton, QTableWidget,
                             QTableWidgetItem, QTabWidget, QVBoxLayout,
                             QWidget)

from core.rbac import (ROLE_PERMISSIONS, AuthError, create_user,
                       reset_password, set_user_active, set_user_role)


class AddUserDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add user")
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.username = QLineEdit()
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.role = QComboBox()
        self.role.addItems(sorted(ROLE_PERMISSIONS))
        form.addRow("Username", self.username)
        form.addRow("Password", self.password)
        form.addRow("Role", self.role)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                   QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class UsersTab(QWidget):
    def __init__(self, db, current_user, parent=None):
        super().__init__(parent)
        self.db = db
        self.current_user = current_user
        layout = QVBoxLayout(self)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            ["Username", "Role", "Active", "Last login"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows)
        layout.addWidget(self.table)

        buttons = QHBoxLayout()
        self.add_btn = QPushButton("Add user…")
        self.toggle_btn = QPushButton("Activate / deactivate")
        self.role_btn = QPushButton("Change role…")
        self.reset_btn = QPushButton("Reset password…")
        can_manage = current_user is not None and \
            current_user.can("user.manage")
        for btn, handler in ((self.add_btn, self.add_user),
                             (self.toggle_btn, self.toggle_active),
                             (self.role_btn, self.change_role),
                             (self.reset_btn, self.reset_pw)):
            btn.setEnabled(can_manage)
            btn.clicked.connect(handler)
            buttons.addWidget(btn)
        layout.addLayout(buttons)
        self.refresh()

    def refresh(self) -> None:
        rows = self.db.list_users()
        self.table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            for col, value in enumerate(
                    [row["username"], row["role"],
                     "yes" if row["active"] else "no",
                     row["last_login"] or "never"]):
                self.table.setItem(i, col, QTableWidgetItem(str(value)))

    def selected_username(self) -> str | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        return self.table.item(row, 0).text()

    def _guard(self, fn) -> None:
        try:
            fn()
            self.refresh()
        except (AuthError, ValueError) as e:
            QMessageBox.warning(self, "User management", str(e))

    def add_user(self) -> None:
        dlg = AddUserDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        self._guard(lambda: create_user(
            self.db, self.current_user, dlg.username.text().strip(),
            dlg.password.text(), dlg.role.currentText()))

    def toggle_active(self) -> None:
        username = self.selected_username()
        if not username:
            return
        active = self.db.get_user(username)["active"]
        self._guard(lambda: set_user_active(
            self.db, self.current_user, username, not active))

    def change_role(self) -> None:
        username = self.selected_username()
        if not username:
            return
        role, ok = QInputDialog.getItem(
            self, "Change role", f"New role for {username}:",
            sorted(ROLE_PERMISSIONS), 0, False)
        if ok:
            self._guard(lambda: set_user_role(
                self.db, self.current_user, username, role))

    def reset_pw(self) -> None:
        username = self.selected_username()
        if not username:
            return
        pw, ok = QInputDialog.getText(
            self, "Reset password", f"New password for {username}:",
            QLineEdit.EchoMode.Password)
        if ok and pw:
            self._guard(lambda: reset_password(
                self.db, self.current_user, username, pw))


class BackupTab(QWidget):
    def __init__(self, backup_service, current_user, parent=None):
        super().__init__(parent)
        self.backup_service = backup_service
        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.dest = QLineEdit(
            str(backup_service.dest_dir) if backup_service else "")
        pick = QPushButton("Browse…")
        pick.clicked.connect(self.pick_dest)
        dest_row = QHBoxLayout()
        dest_row.addWidget(self.dest)
        dest_row.addWidget(pick)
        form.addRow("Backup destination", dest_row)

        self.interval = QDoubleSpinBox()
        self.interval.setRange(0.5, 168)
        self.interval.setValue(
            backup_service.interval_hours if backup_service else 24.0)
        self.interval.setSuffix(" hours")
        form.addRow("Interval", self.interval)
        layout.addLayout(form)

        self.run_btn = QPushButton("Run backup now")
        can_backup = current_user is not None and \
            current_user.can("config.backup")
        self.run_btn.setEnabled(can_backup and backup_service is not None)
        self.run_btn.clicked.connect(self.run_now)
        layout.addWidget(self.run_btn)

        # clean-machine restore needs the master key (UAT §5 / HLD §14.3)
        self.export_key_btn = QPushButton("Export master key…")
        can_manage = current_user is not None and \
            current_user.can("user.manage")
        self.export_key_btn.setEnabled(can_manage and
                                       backup_service is not None)
        self.export_key_btn.clicked.connect(self.export_key)
        layout.addWidget(self.export_key_btn)

        self.result = QLabel("No backup run this session.")
        self.result.setWordWrap(True)
        layout.addWidget(self.result)
        layout.addStretch(1)

    def pick_dest(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Backup destination")
        if path:
            self.dest.setText(path)
            if self.backup_service:
                from pathlib import Path
                self.backup_service.dest_dir = Path(path)

    def run_now(self) -> None:
        if self.backup_service is None:
            return
        self.backup_service.interval_hours = self.interval.value()
        try:
            path = self.backup_service.run_backup()
            self.result.setText(f"Backup OK: {path}")
        except Exception as e:
            self.result.setText(f"Backup FAILED: {e}")

    def export_key(self) -> None:
        """Write the master key as hex for clean-machine restores
        (scripts/restore.py --key-hex). Keep the file OFF this machine."""
        if self.backup_service is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export master key", "dokscanner-master-key.txt",
            "Text files (*.txt)")
        if not path:
            return
        from pathlib import Path
        Path(path).write_text(self.backup_service.master_key.hex(),
                              encoding="utf-8")
        self.backup_service.db.append_audit(
            None, "admin", "key_export", {"path": path})
        QMessageBox.warning(
            self, "Master key exported",
            "The key file unlocks every backup archive.\n"
            "Store it on removable media in a safe location — "
            "NOT on this machine.")


class BurnTab(QWidget):
    """CD/DVD burn queue status + operator burn trigger (Phase 3)."""

    def __init__(self, cddvd_channel, current_user, parent=None):
        super().__init__(parent)
        self.channel = cddvd_channel
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.queue_label = QLabel("")
        form.addRow("Burn queue", self.queue_label)
        self.label_edit = QLineEdit()
        self.label_edit.setPlaceholderText(
            "Disc label (letters, digits, - _ ; empty = timestamp)")
        form.addRow("Disc label", self.label_edit)
        layout.addLayout(form)

        self.burn_btn = QPushButton("Burn disc now")
        can_burn = current_user is not None and \
            current_user.can("config.backup")
        self.burn_btn.setEnabled(can_burn and cddvd_channel is not None)
        self.burn_btn.clicked.connect(self.burn_now)
        layout.addWidget(self.burn_btn)

        self.result = QLabel("" if cddvd_channel is not None else
                             "CD/DVD channel is not enabled in the active "
                             "project (features → cd_burn).")
        self.result.setWordWrap(True)
        layout.addWidget(self.result)
        layout.addStretch(1)
        self.refresh()

    def refresh(self) -> None:
        if self.channel is None:
            self.queue_label.setText("channel disabled")
            return
        self.queue_label.setText(
            f"{self.channel.queue.file_count()} file(s) · "
            f"{self.channel.queue.total_size_mb():.1f} MB staged")

    def burn_now(self) -> None:
        if self.channel is None:
            return
        result = self.channel.burn(self.label_edit.text().strip() or None)
        if result.success:
            self.result.setText(
                f"Burn OK — label {result.destination}, "
                f"{result.detail['files']} file(s), verified="
                f"{result.detail['verified']}")
        else:
            self.result.setText(f"Burn FAILED: {result.detail.get('error') or result.detail}")
        self.refresh()


class AdminPanel(QWidget):
    def __init__(self, db, current_user, backup_service=None,
                 cddvd_channel=None, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        self.users_tab = UsersTab(db, current_user)
        self.backup_tab = BackupTab(backup_service, current_user)
        self.burn_tab = BurnTab(cddvd_channel, current_user)
        tabs.addTab(self.users_tab, "Users")
        tabs.addTab(self.backup_tab, "Backup")
        tabs.addTab(self.burn_tab, "CD/DVD")
        layout.addWidget(tabs)
