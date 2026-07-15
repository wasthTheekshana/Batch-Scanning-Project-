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

    def __init__(self, db, audit, current_user, pipeline=None, parent=None):
        super().__init__(parent)
        self.db = db
        self.audit = audit
        self.current_user = current_user
        self.pipeline = pipeline

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
        # word wrap keeps long QC detail from inflating the window's
        # minimum width beyond the screen (QLabel min width = text width)
        self.score_label.setWordWrap(True)
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
        self.refresh()  # show documents held in previous sessions too

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
        self.score_label.setText(self._summarise_qc(doc))
        original, working = self._first_page_paths(doc)
        if original:
            self.before_view.set_image(original)
        if working:
            self.after_view.set_image(working)

    @staticmethod
    def _first_page_paths(doc) -> tuple[str | None, str | None]:
        """(original_path, working_path) of the document's first page,
        recorded in qc_detail by the QC worker."""
        try:
            pages = json.loads(doc["qc_detail"] or "{}").get("pages", {})
        except json.JSONDecodeError:
            return None, None
        for page_no in sorted(pages, key=lambda k: int(k) if str(k).isdigit() else 0):
            info = pages[page_no]
            if isinstance(info, dict) and (info.get("original_path")
                                           or info.get("working_path")):
                return info.get("original_path"), info.get("working_path")
        return None, None

    @staticmethod
    def _summarise_qc(doc) -> str:
        """Compact human-readable stage summary, e.g.
        'QC score: 70 — page 3: advanced −20 [blur_warn]'."""
        try:
            detail = json.loads(doc["qc_detail"] or "{}")
        except json.JSONDecodeError:
            detail = {}
        parts = []
        for page_no, stages in detail.get("pages", {}).items():
            for stage, info in stages.items():
                if not isinstance(info, dict):
                    continue
                deduction = info.get("deduction", 0)
                if deduction:
                    parts.append(f"page {page_no}: {stage} −{deduction}")
            flags = stages.get("flags") if isinstance(stages, dict) else None
            if flags:
                parts.append(f"page {page_no} flags: {', '.join(flags)}")
        summary = "; ".join(parts) if parts else "no deductions recorded"
        return f"QC score: {doc['qc_score']} — {summary}"

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
        if action == "accept" and self.pipeline is not None:
            # push the approved document through export + delivery
            if not self.pipeline.resubmit_document(doc_id):
                self.score_label.setText(
                    "Accepted, but page images are no longer available — "
                    "export skipped (see log).")
        self.document_actioned.emit(doc_id, action)
        self.refresh()
