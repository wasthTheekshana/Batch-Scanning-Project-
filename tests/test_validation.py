"""Format checks on index values."""
import pytest

from pipeline.validation import kind_for, validate


@pytest.mark.parametrize("value", ["856789012V", "856789012x", "199012345678", "855011234V",
                                   "", "  "])
def test_good_nics_and_blanks_pass(value):
    assert validate("nic", value) is None


@pytest.mark.parametrize("value", ["12345", "85678901VV", "19901234567", "850009999V", "855001234V",
                                   "180012345678", "199099912345"])
def test_bad_nics_are_explained(value):
    assert validate("nic", value)


@pytest.mark.parametrize("value", ["12011990", "12/01/1990", "1-2-1990", "1990-01-12"])
def test_good_dates_pass(value):
    assert validate("date", value) is None


@pytest.mark.parametrize("value", ["31022000", "99/99/1990", "12/01/1850", "abc", "12012099"])
def test_bad_dates_are_explained(value):
    assert validate("date", value)


def test_phone_and_email():
    assert validate("phone", "077 123 4567") is None and validate("phone", "+94771234567") is None
    assert validate("phone", "12345") and validate("phone", "abc")
    assert validate("email", "a.b@example.com") is None
    assert validate("email", "nobody") and validate("email", "a@b")


def test_unknown_kinds_and_none_never_complain():
    assert validate(None, "anything") is None and validate("zip", "x") is None


def test_the_check_is_chosen_from_the_zone_or_guessed_from_its_name():
    assert kind_for({"name": "x", "validate": "nic"}) == "nic"
    assert kind_for({"name": "x", "validate": "bogus"}) is None
    for name, kind in [("nic_number", "nic"), ("joint_nic_number", "nic"),
                       ("date_of_birth", "date"), ("mobile", "phone"),
                       ("email", "email"), ("account_number", None),
                       ("applicant_name", None)]:
        assert kind_for({"name": name}) == kind, name


# -- in the Index editor ------------------------------------------------------------
@pytest.fixture()
def editor(qtbot, tmp_path):
    from data.db import Database
    from ui.index_editor import IndexEditor
    db = Database(tmp_path / "v.db")
    db.insert_project("p", "P", {})
    db.insert_batch("b", "p", "admin")
    db.insert_document("d1", "b", "p", page_range="1", qc_score=90,
                       validation_status="auto_approved", extracted_fields={})
    ed = IndexEditor(db, [{"name": "nic_number"}, {"name": "applicant_name"}])
    qtbot.addWidget(ed)
    ed.document_id = "d1"
    yield ed
    db.close()


def test_a_wrong_value_is_marked_and_explained(editor):
    editor._inputs["nic_number"].setText("123")
    assert editor._inputs["nic_number"].toolTip() and "border" in editor._inputs["nic_number"].styleSheet()
    editor._inputs["nic_number"].setText("856789012V")
    assert editor._inputs["nic_number"].toolTip() == "" and editor._inputs["nic_number"].styleSheet() == ""


def test_saving_a_wrong_value_asks_first(editor, monkeypatch):
    editor._inputs["nic_number"].setText("123")
    asked = []
    monkeypatch.setattr(editor, "_confirm_invalid", lambda p: asked.append(p) or False)
    editor.save()
    assert list(asked[0]) == ["nic_number"]
    assert "nic_number" not in (editor.db.get_document("d1")["extracted_fields"] or "")
    monkeypatch.setattr(editor, "_confirm_invalid", lambda p: True)       # save anyway
    editor.save()
    assert '"123"' in editor.db.get_document("d1")["extracted_fields"]


def test_a_correct_value_saves_without_asking(editor, monkeypatch):
    editor._inputs["nic_number"].setText("856789012V")
    monkeypatch.setattr(editor, "_confirm_invalid", lambda p: pytest.fail("asked"))
    editor.save()
    assert "856789012V" in editor.db.get_document("d1")["extracted_fields"]


def test_fields_the_reader_was_unsure_of_are_highlighted_until_saved(editor):
    import json
    editor.db.update_document("d1", extracted_fields={"nic_number": "856789012V",
                                                      "applicant_name": "N Perera"},
                              qc_detail={"low_conf_fields": ["applicant_name"]})
    editor.load_document("d1")
    name, nic = editor._inputs["applicant_name"], editor._inputs["nic_number"]
    assert "background" in name.styleSheet() and "low confidence" in name.toolTip()
    assert nic.styleSheet() == ""
    editor.save()
    assert name.styleSheet() == ""
    assert "low_conf_fields" not in json.loads(editor.db.get_document("d1")["qc_detail"])


def test_a_wrong_format_outranks_the_low_confidence_mark(editor):
    editor.db.update_document("d1", extracted_fields={"nic_number": "123"},
                              qc_detail={"low_conf_fields": ["nic_number"]})
    editor.load_document("d1")
    assert "border" in editor._inputs["nic_number"].styleSheet()


def test_the_pipeline_records_which_fields_were_read_with_low_confidence(tmp_path):
    import json

    from pipeline.job_queue import Pipeline
    from data.db import Database
    from delivery.local_folder import LocalFolderChannel
    from export.batch_exporter import export_document
    from ocr.tesseract_engine import OCRResult
    from tests.test_pipeline import make_profile, wait_for, write_pages
    db = Database(tmp_path / "lc.db")
    db.insert_project("proj-1", "P", {})
    profile = make_profile(tmp_path)
    profile["separation"] = {"strategy": ["fixed_pages"], "fixed_pages": 1,
                             "blank_threshold": 0.02}
    profile["features"]["auto_index"] = True
    profile["ocr"] = {"lang": "eng", "mode": "full_page", "confidence_threshold": 0.5,
                      "zones": [{"name": "invoice_no", "regex": r"No:\s*(\S+)"},
                                {"name": "ref", "regex": r"Ref:\s*(\S+)"}]}
    pipeline = Pipeline(db, profile, [LocalFolderChannel(profile["delivery"]["local_folder"])],
                        export_document, working_dir=tmp_path / "staging",
                        ocr_fn=lambda image: OCRResult("Invoice No: INV-1", 0.95))
    jobs = write_pages(tmp_path, ["text"])
    pipeline.start()
    try:
        pipeline.submit_batch("batch-1", jobs, operator="admin")
        assert wait_for(lambda: db.list_documents("batch-1"))
        detail = json.loads(db.list_documents("batch-1")[0]["qc_detail"])
        assert detail["low_conf_fields"] == ["ref"]            # not found on the page
    finally:
        pipeline.shutdown()
        db.close()
