"""Barcode handling: cover sheet vs. barcode on a document's first page,
a pattern filter, and decoding a barcode in any orientation."""
import cv2
import numpy as np
import pytest
import qrcode

from separation.barcode import decode_barcodes, filter_barcodes
from separation.splitter import split_documents


def page(no, barcode=None, text="", blank=False):
    return {"page_no": no, "image": None, "is_blank": blank,
            "barcodes": ([{"type": "CODE128", "value": barcode, "rect": (0, 0, 9, 9)}]
                         if barcode else []),
            "ocr_text": text}


def numbers(doc):
    return [p["page_no"] for p in doc["pages"]]


# -- split_documents --------------------------------------------------------
def test_first_page_role_keeps_the_barcode_page_in_its_document():
    docs = split_documents(
        [page(1, "CIF-1", "NATIONAL IDENTITY CARD"), page(2), page(3, "CIF-2")],
        {"strategy": ["barcode"]})
    assert [numbers(d) for d in docs] == [[1, 2], [3]]
    assert [d["barcode"] for d in docs] == ["CIF-1", "CIF-2"]


def test_cover_sheet_role_labels_the_document_but_drops_the_cover_page():
    docs = split_documents(
        [page(1, "CIF-1"), page(2, text="NIC"), page(3, text="APP"),
         page(4, "CIF-2"), page(5, text="NIC")],
        {"strategy": ["barcode"], "barcode_role": "cover_sheet"})
    assert [numbers(d) for d in docs] == [[2, 3], [5]]
    assert [d["barcode"] for d in docs] == ["CIF-1", "CIF-2"]


def test_a_cover_with_no_pages_after_it_makes_no_empty_document():
    docs = split_documents(
        [page(1, "CIF-1"), page(2, "CIF-2"), page(3, text="NIC")],
        {"strategy": ["barcode"], "barcode_role": "cover_sheet"})
    assert [numbers(d) for d in docs] == [[3]]
    assert docs[0]["barcode"] == "CIF-2"


def test_auto_role_treats_a_barcode_only_page_as_a_cover():
    docs = split_documents(
        [page(1, "CIF-1", text="DOCUMENT COVER SHEET CIF-1"), page(2, text="NIC")],
        {"strategy": ["barcode"], "barcode_role": "auto"})
    assert [numbers(d) for d in docs] == [[2]]
    assert docs[0]["barcode"] == "CIF-1"


def test_auto_role_keeps_a_page_that_has_a_barcode_sticker_and_real_content():
    content = ("PERSONAL LOAN APPLICATION FORM Application No: APP-55001 "
               "Applicant Name: Nimal Perera Product: Personal Loan CIF-1")
    docs = split_documents(
        [page(1, "CIF-1", text=content), page(2, text="declaration")],
        {"strategy": ["barcode"], "barcode_role": "auto"})
    assert [numbers(d) for d in docs] == [[1, 2]]
    assert docs[0]["barcode"] == "CIF-1"


def test_cover_threshold_is_configurable():
    cfg = {"strategy": ["barcode"], "barcode_role": "auto", "cover_max_chars": 5}
    docs = split_documents(
        [page(1, "CIF-1", text="CIF-1 cover sheet"), page(2)], cfg)
    assert numbers(docs[0]) == [1, 2]            # 10 chars of text > 5


def test_pages_before_the_first_barcode_form_their_own_unlabelled_document():
    docs = split_documents(
        [page(1, text="loose"), page(2, "CIF-1"), page(3)],
        {"strategy": ["barcode"], "barcode_role": "cover_sheet"})
    assert [numbers(d) for d in docs] == [[1], [3]]
    assert [d["barcode"] for d in docs] == [None, "CIF-1"]


def test_cover_roles_combine_with_blank_page_separators():
    docs = split_documents(
        [page(1, "CIF-1"), page(2, text="NIC"), page(3, blank=True),
         page(4, text="LETTER")],
        {"strategy": ["barcode", "blank_page"], "barcode_role": "cover_sheet"})
    assert [numbers(d) for d in docs] == [[2], [4]]
    assert [d["barcode"] for d in docs] == ["CIF-1", None]


# -- filter_barcodes --------------------------------------------------------
def bc(value, kind="CODE128"):
    return {"type": kind, "value": value, "rect": (0, 0, 1, 1)}


def test_no_pattern_and_no_types_keeps_every_barcode():
    found = [bc("A"), bc("B", "QRCODE")]
    assert filter_barcodes(found, {}) == found


def test_pattern_keeps_only_matching_values():
    found = [bc("CIF-100234"), bc("8901234567890", "EAN13"), bc("cif-1")]
    kept = filter_barcodes(found, {"barcode_pattern": r"^CIF-\d+$"})
    assert [b["value"] for b in kept] == ["CIF-100234"]


def test_types_keep_only_the_listed_symbologies():
    found = [bc("A", "CODE128"), bc("B", "QRCODE"), bc("C", "EAN13")]
    kept = filter_barcodes(found, {"barcode_types": ["QR", "Code128"]})
    assert [b["value"] for b in kept] == ["A", "B"]


def test_a_broken_pattern_matches_nothing_instead_of_crashing():
    assert filter_barcodes([bc("A")], {"barcode_pattern": "("}) == []


# -- decoding in any orientation ---------------------------------------------
def qr_page(value, rotation=None):
    img = np.array(qrcode.make(value).convert("L"))
    canvas = np.full((900, 700), 255, np.uint8)
    canvas[100:100 + img.shape[0], 100:100 + img.shape[1]] = img
    return canvas if rotation is None else cv2.rotate(canvas, rotation)


@pytest.mark.parametrize("rotation", [None, cv2.ROTATE_180,
                                      cv2.ROTATE_90_CLOCKWISE,
                                      cv2.ROTATE_90_COUNTERCLOCKWISE])
def test_a_barcode_is_found_whichever_way_the_page_was_fed(rotation):
    found = decode_barcodes(qr_page("CIF-100234", rotation))
    assert [b["value"] for b in found] == ["CIF-100234"]


def test_a_page_without_a_barcode_returns_nothing():
    assert decode_barcodes(np.full((300, 300), 255, np.uint8)) == []


# -- through the real pipeline ---------------------------------------------------
def _run(tmp_path, db, separation, barcodes_by_page, specs, monkeypatch,
         sparse_pages=()):
    """Run a batch with a fake barcode decoder: barcodes_by_page maps a
    page number to the barcode values 'found' on it. Each page file is
    stamped with its number so the fake works whatever order the pipeline
    threads handle the pages in."""
    import core.job_queue as jq
    from tests.test_pipeline import (build_pipeline, make_profile, wait_for,
                                     write_pages)
    profile = make_profile(tmp_path)
    profile["separation"] = {"blank_threshold": 0.02, **separation}
    pipeline = build_pipeline(tmp_path, db, profile)
    jobs = write_pages(tmp_path, specs)
    for j in jobs:
        j.batch_id = "batch-1"
        img = cv2.imread(j.image_path, cv2.IMREAD_UNCHANGED)
        img[5, 5] = 100 + j.page_no
        if j.page_no in sparse_pages:      # a short note: ~1.6% ink, not empty
            h, w = img.shape[:2]
            img[h // 2:h // 2 + int(h * 0.016), w // 4:w // 4 + w // 2] = 0
        cv2.imwrite(j.image_path, img)

    def fake_decode(image):
        page_no = int(image[5, 5]) - 100
        return [{"type": "CODE128", "value": v, "rect": (0, 0, 9, 9)}
                for v in barcodes_by_page.get(page_no, [])]

    monkeypatch.setattr(jq, "decode_barcodes", fake_decode)
    pipeline.start()
    try:
        pipeline.submit_batch("batch-1", jobs)
        assert wait_for(lambda: len(db.list_documents("batch-1")) >= 1)
        wait_for(lambda: all(d["validation_status"] != "pending_qc"
                             or d["qc_detail"]
                             for d in db.list_documents("batch-1")))
    finally:
        pipeline.shutdown()
    return db.list_documents("batch-1")


@pytest.fixture()
def db(tmp_path):
    from data.db import Database
    d = Database(tmp_path / "br.db")
    d.insert_project("proj-1", "Test Project", {})
    yield d
    d.close()


def _page_counts(docs):
    import json
    return sorted(len(json.loads(d["qc_detail"])["pages"]) for d in docs)


def test_cover_sheet_pages_never_reach_the_document_or_its_qc(tmp_path, db, monkeypatch):
    docs = _run(tmp_path, db,
                {"strategy": ["barcode"], "barcode_role": "cover_sheet"},
                {1: ["CIF-1"], 4: ["CIF-2"]},
                ["text", "text", "text", "text", "text"], monkeypatch)
    assert _page_counts(docs) == [1, 2]          # pages 2-3 and page 5
    import json
    assert sorted(json.loads(d["extracted_fields"])["barcode"]
                  for d in docs) == ["CIF-1", "CIF-2"]


def test_first_page_role_keeps_the_barcode_page(tmp_path, db, monkeypatch):
    docs = _run(tmp_path, db, {"strategy": ["barcode"]},
                {1: ["CIF-1"], 4: ["CIF-2"]},
                ["text", "text", "text", "text", "text"], monkeypatch)
    assert _page_counts(docs) == [2, 3]


def test_a_barcode_that_does_not_match_the_pattern_does_not_split(tmp_path, db, monkeypatch):
    docs = _run(tmp_path, db,
                {"strategy": ["barcode"], "barcode_pattern": r"^CIF-\d+$"},
                {1: ["CIF-1"], 2: ["8901234567890"], 4: ["CIF-2"]},
                ["text", "text", "text", "text"], monkeypatch)
    assert _page_counts(docs) == [1, 3]          # page 2's barcode ignored


# -- discarding blank pages without splitting -------------------------------------------
def test_blank_pages_are_discarded_without_splitting_the_document():
    docs = split_documents(
        [page(1, "CIF-1"), page(2, text="NIC"), page(3, blank=True),
         page(4, text="APP"), page(5, blank=True), page(6, text="LETTER")],
        {"strategy": ["barcode"], "barcode_role": "cover_sheet",
         "drop_blank_pages": True})
    assert [numbers(d) for d in docs] == [[2, 4, 6]]
    assert docs[0]["barcode"] == "CIF-1"


def test_without_the_option_blank_pages_stay_in_the_document():
    docs = split_documents(
        [page(1, "CIF-1"), page(2, text="NIC"), page(3, blank=True)],
        {"strategy": ["barcode"], "barcode_role": "cover_sheet"})
    assert [numbers(d) for d in docs] == [[2, 3]]


def test_the_blank_page_strategy_still_splits_when_both_are_set():
    docs = split_documents(
        [page(1, text="A"), page(2, blank=True), page(3, text="B")],
        {"strategy": ["blank_page"], "drop_blank_pages": True})
    assert [numbers(d) for d in docs] == [[1], [3]]


def test_a_page_with_a_barcode_is_never_dropped_as_blank():
    docs = split_documents(
        [page(1, "CIF-1", blank=True), page(2, text="NIC")],
        {"strategy": ["barcode"], "drop_blank_pages": True})
    assert [numbers(d) for d in docs] == [[1, 2]]


def test_dropping_blanks_keeps_fixed_page_counts_to_real_pages():
    docs = split_documents(
        [page(1, text="a"), page(2, blank=True), page(3, text="b"),
         page(4, blank=True), page(5, text="c"), page(6, blank=True)],
        {"strategy": ["fixed_pages"], "fixed_pages": 2, "drop_blank_pages": True})
    assert [numbers(d) for d in docs] == [[1, 3], [5]]


def test_a_batch_of_only_blank_pages_makes_no_documents():
    assert split_documents([page(1, blank=True), page(2, blank=True)],
                           {"strategy": ["barcode"], "drop_blank_pages": True}) == []


def test_through_the_pipeline_the_blank_backs_are_not_exported(tmp_path, db, monkeypatch):
    docs = _run(tmp_path, db,
                {"strategy": ["barcode"], "barcode_role": "cover_sheet",
                 "drop_blank_pages": True},
                {1: ["CIF-1"]},
                ["text", "text", "blank", "text", "blank"], monkeypatch)
    assert _page_counts(docs) == [2]               # only the two printed pages


def page_with_ink(no, density, **kw):
    p = page(no, **kw)
    p["is_blank"] = density < 0.02            # what the 2% separator test says
    p["ink_density"] = density
    return p


def test_a_sparse_page_is_kept_but_an_empty_one_is_discarded():
    docs = split_documents(
        [page_with_ink(1, 0.0005), page_with_ink(2, 0.016, text="note"),
         page_with_ink(3, 0.0001), page_with_ink(4, 0.08, text="form")],
        {"strategy": ["barcode"], "drop_blank_pages": True})
    assert [numbers(d) for d in docs] == [[2, 4]]


def test_the_discard_limit_can_be_changed():
    pages = [page_with_ink(1, 0.01, text="x"), page_with_ink(2, 0.08, text="y")]
    strict = {"strategy": ["barcode"], "drop_blank_pages": True}
    assert [numbers(d) for d in split_documents(pages, strict)] == [[1, 2]]
    loose = dict(strict, drop_blank_threshold=0.02)
    assert [numbers(d) for d in split_documents(pages, loose)] == [[2]]


def test_through_the_pipeline_a_sparse_page_is_kept_and_read(tmp_path, db, monkeypatch):
    docs = _run(tmp_path, db,
                {"strategy": ["barcode"], "barcode_role": "cover_sheet",
                 "drop_blank_pages": True},
                {1: ["CIF-1"]},
                ["text", "text", "blank", "text", "blank"], monkeypatch,
                sparse_pages=(4,))
    assert _page_counts(docs) == [2]               # page 2 and the sparse page 4


def test_every_page_left_out_of_a_document_is_audited_with_its_reason():
    import types

    from core.job_queue import Pipeline

    class Audit:
        def __init__(self):
            self.events = []

        def log(self, action, user, **detail):
            self.events.append((action, user, detail))

    audit = Audit()
    page_dicts = [
        {"page_no": 1, "barcodes": [{"value": "CIF-1"}], "is_custody_label": False},
        {"page_no": 2, "barcodes": [], "is_custody_label": False},
        {"page_no": 3, "barcodes": [], "is_custody_label": False},     # blank back
        {"page_no": 4, "barcodes": [{"value": "DOK-BUNDLE-X"}], "is_custody_label": True},
    ]
    docs = [{"pages": [page_dicts[1]], "barcode": "CIF-1"}]
    Pipeline._audit_excluded_pages(types.SimpleNamespace(audit=audit), "b1",
                                   page_dicts, docs)
    action, user, detail = audit.events[0]
    assert (action, user, detail["batch_id"], detail["count"]) == (
        "pages_excluded", "system", "b1", 3)
    assert detail["reasons"] == {1: "separator", 3: "blank", 4: "custody_label"}


def test_nothing_is_audited_when_every_page_is_in_a_document():
    import types

    from core.job_queue import Pipeline
    audit = types.SimpleNamespace(log=lambda *a, **k: (_ for _ in ()).throw(AssertionError))
    pages = [{"page_no": 1, "barcodes": [], "is_custody_label": False}]
    Pipeline._audit_excluded_pages(types.SimpleNamespace(audit=audit), "b1", pages,
                                   [{"pages": pages, "barcode": None}])
