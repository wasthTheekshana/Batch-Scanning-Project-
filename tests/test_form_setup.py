"""Setting up a form from a blank copy: anchor words are learned in the Designer."""
import cv2
import numpy as np
import pytest

from ocr.sample import describe, learn_anchors, load_sample_image


class FakeResult:
    def __init__(self, words):
        self.words = words


def word(text, x, y):
    return {"text": text, "left": x, "top": y, "width": 100, "height": 20}


BLANK = [word(t, 50 + 120 * i, 40 + 60 * (i // 5)) for i, t in enumerate(
    "Application Applicant Surname Address Telephone Occupation Employer "
    "Salary Guarantor Signature".split())]


def test_anchor_words_are_the_forms_printed_words_with_their_centres():
    anchors = learn_anchors(np.zeros((10, 10), np.uint8), lambda img: FakeResult(BLANK))
    by = {a["t"]: a for a in anchors}
    assert by["surname"] == {"t": "surname", "x": 50 + 120 * 2 + 50, "y": 40 + 60 * 0 + 10}
    assert len(anchors) == 10


def test_repeated_and_short_words_are_not_anchors():
    words = [word("the", 0, 0), word("Name", 0, 30)] + [word("Name", 200 * i, 80) for i in range(4)]
    assert learn_anchors(np.zeros((5, 5), np.uint8), lambda i: FakeResult(words)) == []


def test_describe_says_how_usable_the_anchors_are():
    few = [{"t": f"w{i}", "x": 0, "y": 0} for i in range(2)]
    some = [{"t": f"w{i}", "x": 0, "y": 0} for i in range(8)]
    many = [{"t": f"w{i}", "x": 0, "y": 0} for i in range(20)]
    assert "too few" in describe(few) and "straight" in describe(some)
    assert "will line up" in describe(many)


def test_sample_image_loads_images_and_pdfs(tmp_path):
    png = tmp_path / "b.png"
    cv2.imwrite(str(png), np.full((30, 40), 255, np.uint8))
    assert load_sample_image(png).shape[:2] == (30, 40)
    import fitz
    pdf = tmp_path / "b.pdf"
    with fitz.open() as doc:
        doc.new_page(width=100, height=50)
        doc.save(str(pdf))
    img = load_sample_image(pdf)
    assert img.shape[:2] == (round(50 / 72 * 300), round(100 / 72 * 300))[::-1] or img.shape[1] > img.shape[0]
    assert load_sample_image(pdf, page=3) is None
    assert load_sample_image(tmp_path / "missing.png") is None


def test_the_dialog_learns_keeps_and_returns_anchors(qtbot, tmp_path):
    from ui.document_type_dialog import DocumentTypeDialog
    png = tmp_path / "b.png"
    cv2.imwrite(str(png), np.full((30, 40), 255, np.uint8))
    dlg = DocumentTypeDialog()
    qtbot.addWidget(dlg)
    assert "No blank form" in dlg.anchor_label.text()
    dlg.learn_from_blank(str(png), lambda img: FakeResult(BLANK))
    assert "10 anchor words" in dlg.anchor_label.text()
    dlg.name.setText("Loan")
    dlg.regex.setText("loan")
    dlg._append_zone({"name": "n", "x": 1, "y": 1, "w": 5, "h": 5})
    built = dlg.build_type()
    assert len(built["anchors"]) == 10
    again = DocumentTypeDialog(built)                      # editing keeps them
    qtbot.addWidget(again)
    assert again.build_type()["anchors"] == built["anchors"]


def test_a_type_without_a_learned_form_has_no_anchors_key(qtbot):
    from ui.document_type_dialog import DocumentTypeDialog
    dlg = DocumentTypeDialog()
    qtbot.addWidget(dlg)
    dlg.name.setText("NIC")
    dlg.regex.setText("identity")
    dlg._append_zone({"name": "n", "regex": "x"})
    assert "anchors" not in dlg.build_type()
