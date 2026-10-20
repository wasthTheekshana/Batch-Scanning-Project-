"""A document of several pages can be paged through in the QC review and Index tabs (they showed only page 1)."""
import cv2
import numpy as np
import pytest

from core.rbac import User
from data.audit import AuditLogger
from data.db import Database
from ui.image_viewer import PageNav, document_pages
from ui.index_editor import IndexEditor
from ui.qc_review import QCReviewPanel

ADMIN = User("a1", "root", "admin")
WIDTHS = [300, 400, 500]                 # each page has its own width, so the shown page can be told apart


@pytest.fixture()
def env(tmp_path):
    db = Database(tmp_path / "nav.db")
    audit = AuditLogger(db, tmp_path / "logs")
    db.insert_project("p1", "P", {})
    db.insert_batch("b1", "p1", "op")
    pages = {}
    for n, width in enumerate(WIDTHS, start=1):
        for kind in ("original", "working"):
            cv2.imwrite(str(tmp_path / f"{kind}{n}.png"), np.full((200, width, 3), 200, np.uint8))
        pages[str(n)] = {"original_path": str(tmp_path / f"original{n}.png"), "working_path": str(tmp_path / f"working{n}.png")}
    db.insert_document("multi", "b1", "p1", page_range="1-3", qc_score=60, validation_status="pending_qc",
                       qc_detail={"flags": [], "pages": pages}, extracted_fields={})
    db.insert_document("single", "b1", "p1", page_range="4-4", qc_score=90, validation_status="auto_approved",
                       qc_detail={"flags": [], "pages": {"4": pages["1"]}}, extracted_fields={})
    yield db, audit
    audit.close()
    db.close()


def shown_width(view):
    return view._item.pixmap().width()


def test_the_pages_of_a_document_are_listed_in_order_and_pages_without_an_image_are_left_out(env):
    db, _audit = env
    pages = document_pages(db.get_document("multi"))
    assert [p["no"] for p in pages] == ["1", "2", "3"]
    assert document_pages({"qc_detail": "{}"}) == [] and document_pages({"qc_detail": "not json"}) == []


def test_the_page_buttons_move_through_the_pages_and_stop_at_both_ends(qtbot):
    nav = PageNav()
    qtbot.addWidget(nav)
    seen = []
    nav.changed.connect(seen.append)
    nav.set_count(3)
    assert nav.label.text() == "Page 1 of 3" and not nav.prev_btn.isEnabled() and nav.next_btn.isEnabled()
    nav.next_btn.click()
    nav.next_btn.click()
    nav.next_btn.click()                                           # already on the last page
    assert seen == [1, 2] and nav.label.text() == "Page 3 of 3" and not nav.next_btn.isEnabled()
    nav.prev_btn.click()
    assert seen == [1, 2, 1]
    nav.set_count(1)
    assert nav.isHidden()                                          # nothing to page through


def test_qc_review_shows_every_page_of_the_selected_document(qtbot, env):
    db, _audit = env
    panel = QCReviewPanel(db, None, ADMIN)
    qtbot.addWidget(panel)
    panel.show()
    ids = [panel.queue.item(i).data(0x0100) for i in range(panel.queue.count())]
    panel.queue.setCurrentRow(ids.index("multi"))
    assert panel.page_nav.isVisible() and panel.page_nav.label.text() == "Page 1 of 3"
    assert shown_width(panel.before_view) == WIDTHS[0] and shown_width(panel.after_view) == WIDTHS[0]
    panel.page_nav.next_btn.click()
    assert shown_width(panel.before_view) == WIDTHS[1] and shown_width(panel.after_view) == WIDTHS[1]
    panel.page_nav.next_btn.click()
    assert shown_width(panel.before_view) == WIDTHS[2]
    panel.queue.setCurrentRow(ids.index("single"))                 # a one-page document: no paging, page 1 shown
    assert not panel.page_nav.isVisible() and shown_width(panel.before_view) == WIDTHS[0]


def test_the_index_tab_shows_every_page_of_the_selected_document(qtbot, env):
    db, audit = env
    editor = IndexEditor(db, [], audit, ADMIN)
    qtbot.addWidget(editor)
    editor.show()
    editor.select_document("multi")
    assert editor.page_nav.isVisible() and editor.page_nav.label.text() == "Page 1 of 3"
    assert shown_width(editor.page_view) == WIDTHS[0]
    editor.page_nav.next_btn.click()
    assert shown_width(editor.page_view) == WIDTHS[1]
    editor.page_nav.next_btn.click()
    assert shown_width(editor.page_view) == WIDTHS[2]
    editor.select_document("single")                               # opening another document starts on its page 1
    assert not editor.page_nav.isVisible() and shown_width(editor.page_view) == WIDTHS[0]
