"""QC review panel — WARN/FAIL queue with before/after view and
accept / reject / rescan actions (HLD §16, §5 score routing)."""
from __future__ import annotations

import json
import logging

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QListWidget,
                             QListWidgetItem, QPushButton, QSplitter,
                             QVBoxLayout, QWidget)

from core.job_queue import REASON_NO_PAGE_IMAGES
from ui.flow import FlowLayout
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
        left_layout.addWidget(QLabel("Documents"))
        self.filter_combo = QComboBox()
        self.filter_combo.addItem("Review queue (WARN / FAIL)", "pending")
        self.filter_combo.addItem("All documents", "all")
        self.filter_combo.currentIndexChanged.connect(lambda _: self.refresh())
        left_layout.addWidget(self.filter_combo)
        self.queue = QListWidget()
        from ui.theme import tidy_list
        tidy_list(self.queue, 250)
        self.queue.currentItemChanged.connect(self._show_document)
        left_layout.addWidget(self.queue)
        splitter.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        views = QHBoxLayout()
        self.before_view = ImageView()
        self.after_view = ImageView()
        for view in (self.before_view, self.after_view):
            view.setMinimumSize(240, 240)
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

        buttons = FlowLayout()
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
        self.edit_btn = QPushButton("Edit page…")
        self.edit_btn.setEnabled(can_review)
        self.edit_btn.clicked.connect(self.open_editor)
        buttons.addWidget(self.edit_btn)
        self.split_merge_btn = QPushButton("Split / merge…")
        self.split_merge_btn.setToolTip(
            "The automatic separation got it wrong: cut this document in two, or join it "
            "with another document of the same batch.")
        self.split_merge_btn.setEnabled(can_review)
        self.split_merge_btn.clicked.connect(self.open_split_merge)
        buttons.addWidget(self.split_merge_btn)
        from ui.theme import mark
        mark(self.accept_btn, "primary")
        mark(self.reject_btn, "danger")
        right_layout.addLayout(buttons)
        splitter.addWidget(right)
        splitter.setChildrenCollapsible(False)
        splitter.setSizes([280, 780])
        self.refresh()  # show documents held in previous sessions too

    # -- data ----------------------------------------------------------------
    def refresh(self) -> None:
        self.queue.clear()
        mode = self.filter_combo.currentData()
        if mode == "all":
            docs = self.db.list_documents()
        else:
            docs = self.db.list_documents(validation_status="pending_qc")
        from PyQt6.QtGui import QBrush, QColor

        from ui.theme import STATUS_COLORS, two_lines
        for doc in docs:
            item = QListWidgetItem()
            two_lines(item, f"{doc['document_id'][:8]} · pages {doc['page_range']}",
                      f"score {doc['qc_score']} · {doc['validation_status']}")
            item.setData(Qt.ItemDataRole.UserRole, doc["document_id"])
            color = STATUS_COLORS.get(doc["validation_status"])
            if color:
                item.setForeground(QBrush(QColor(color)))
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

    def editable_pages(self, doc_id: str) -> dict[str, dict]:
        """page_no -> {original_path, working_path} for pages whose
        working image still exists on disk."""
        from pathlib import Path
        doc = self.db.get_document(doc_id)
        if doc is None:
            return {}
        try:
            pages = json.loads(doc["qc_detail"] or "{}").get("pages", {})
        except json.JSONDecodeError:
            return {}
        return {no: {"original_path": info.get("original_path"),
                     "working_path": info.get("working_path")}
                for no, info in pages.items()
                if isinstance(info, dict) and info.get("working_path")
                and Path(info["working_path"]).exists()}

    def open_split_merge(self) -> None:
        doc_id = self.current_document_id()
        if doc_id is None:
            return
        from ui.split_merge_dialog import SplitMergeDialog
        user = self.current_user.username if self.current_user else "unknown"
        dlg = SplitMergeDialog(self.db, self.audit, user, doc_id, parent=self)
        dlg.exec()
        if dlg.result_edit is not None:
            self.filter_combo.setCurrentIndex(self.filter_combo.findData("pending"))
            self.refresh()
            first_new = dlg.result_edit.new_ids[0]
            for row in range(self.queue.count()):
                if self.queue.item(row).data(Qt.ItemDataRole.UserRole) == first_new:
                    self.queue.setCurrentRow(row)
                    break

    def open_editor(self) -> None:
        doc_id = self.current_document_id()
        if doc_id is None:
            return
        pages = self.editable_pages(doc_id)
        if not pages:
            self.score_label.setText(
                "No editable page images for this document.")
            return
        from ui.page_editor import PageEditDialog
        user = self.current_user.username if self.current_user else "unknown"
        dlg = PageEditDialog(doc_id, pages, audit=self.audit,
                             username=user, parent=self)
        dlg.saved.connect(lambda *_: self._show_document(
            self.queue.currentItem(), None))
        dlg.exec()

    def _action(self, action: str) -> None:
        doc_id = self.current_document_id()
        if doc_id is None:
            return
        if action == "accept" and self.pipeline is not None:
            # decide BEFORE touching the status: a refusal must leave the
            # document in the review queue so it can be retried after the
            # project is fixed. Missing page images still let the approval
            # through (export is skipped with a message below).
            ok, reason = self.pipeline.can_resubmit(doc_id)
            if not ok and reason != REASON_NO_PAGE_IMAGES:
                self.score_label.setText(
                    f"Not accepted: {reason}. The document stays in the "
                    "review queue; fix the project and accept it again.")
                return
        status = {"accept": "corrected", "reject": "rejected",
                  "rescan": "rejected"}[action]
        self.db.update_document(doc_id, validation_status=status)
        user = self.current_user.username if self.current_user else "unknown"
        try:    # a decided document may complete its custody bundle's QC
            from custody.bundle_manager import refresh_for_document
            refresh_for_document(self.db, doc_id, user, audit=self.audit)
        except Exception:
            logging.getLogger(__name__).exception("custody stage update failed")
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
