"""QC review panel — WARN/FAIL queue with before/after view and
accept / reject / rescan actions (HLD §16, §5 score routing)."""
from __future__ import annotations

import json

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QHBoxLayout, QLabel, QListWidget,
                             QListWidgetItem, QPushButton, QSplitter,
                             QVBoxLayout, QWidget)

from ui.image_viewer import ImageView


class QCReviewPanel(QWidget):
    document_actioned = pyqtSignal(str, str)  # document_id, action

    def __init__(self, db, audit, current_user, parent=None):
        super().__init__(parent)
        self.db = db
        self.audit = audit
        self.current_user = current_user

        layout = QHBoxLayout(self)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(QLabel("Review queue (WARN / FAIL)"))
        self.queue = QListWidget()
        self.queue.currentItemChanged.connect(self._show_document)
        left_layout.addWidget(self.queue)
        splitter.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        views = QHBoxLayout()
        self.before_view = ImageView()
        self.after_view = ImageView()
        for label, view in (("Original", self.before_view),
                            ("After QC fixes", self.after_view)):
            col = QVBoxLayout()
            col.addWidget(QLabel(label))
            col.addWidget(view)
            views.addLayout(col)
        right_layout.addLayout(views)

        self.score_label = QLabel("")
        right_layout.addWidget(self.score_label)

        buttons = QHBoxLayout()
        self.accept_btn = QPushButton("Accept → export")
        self.reject_btn = QPushButton("Reject")
        self.rescan_btn = QPushButton("Flag for rescan")
        can_review = current_user is not None and current_user.can("qc.review")
        for btn, action in ((self.accept_btn, "accept"),
                            (self.reject_btn, "reject"),
                            (self.rescan_btn, "rescan")):
            btn.setEnabled(can_review)
            btn.clicked.connect(lambda _=False, a=action: self._action(a))
            buttons.addWidget(btn)
        right_layout.addLayout(buttons)
        splitter.addWidget(right)
        splitter.setSizes([250, 750])

    # -- data ----------------------------------------------------------------
    def refresh(self) -> None:
        self.queue.clear()
        for doc in self.db.list_documents(validation_status="pending_qc"):
            item = QListWidgetItem(
                f"{doc['document_id'][:8]} · pages {doc['page_range']} · "
                f"score {doc['qc_score']}")
            item.setData(Qt.ItemDataRole.UserRole, doc["document_id"])
            self.queue.addItem(item)

    def current_document_id(self) -> str | None:
        item = self.queue.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _show_document(self, current, _previous) -> None:
        if current is None:
            return
        doc = self.db.get_document(current.data(Qt.ItemDataRole.UserRole))
        if doc is None:
            return
        self.score_label.setText(
            f"QC score: {doc['qc_score']} — detail: "
            f"{json.dumps(json.loads(doc['qc_detail'] or '{}'))[:400]}")
        if doc["storage_path"]:
            self.after_view.set_image(doc["storage_path"])

    def _action(self, action: str) -> None:
        doc_id = self.current_document_id()
        if doc_id is None:
            return
        status = {"accept": "corrected", "reject": "rejected",
                  "rescan": "rejected"}[action]
        self.db.update_document(doc_id, validation_status=status)
        user = self.current_user.username if self.current_user else "unknown"
        if self.audit:
            self.audit.log("qc_review", user, document_id=doc_id,
                           decision=action)
        self.document_actioned.emit(doc_id, action)
        self.refresh()
