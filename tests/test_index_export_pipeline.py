"""The index file goes through the real pipeline: written next to the PDF,
delivered with it, never able to fail the document."""
import csv
import io
import json
import xml.etree.ElementTree as ET

import pytest

from pipeline.job_queue import Pipeline
from data.audit import AuditLogger
from data.db import Database
from delivery.base import DeliveryResult
from delivery.local_folder import LocalFolderChannel
from export.batch_exporter import export_document
from ocr.tesseract_engine import OCRResult
from tests.test_pipeline import make_profile, wait_for, write_pages


@pytest.fixture()
def env(tmp_path):
    db = Database(tmp_path / "ix.db")
    db.insert_project("proj-1", "Test Project", {})
    audit = AuditLogger(db, tmp_path / "logs")
    yield db, audit, tmp_path
    audit.close()
    db.close()


def run(env, index_cfg, channel=None, pages=2, fmt="image_pdf", keep=False):
    db, audit, tmp_path = env
    profile = make_profile(tmp_path)
    profile["separation"] = {"strategy": ["fixed_pages"], "fixed_pages": pages,
                             "blank_threshold": 0.02}
    profile["export"]["format"] = fmt
    profile["export"]["filename_template"] = "{batch_id}_{counter:04d}"
    if index_cfg is not None:
        profile["export"]["index"] = index_cfg
    profile["features"]["auto_index"] = True
    profile["ocr"] = {"lang": "eng", "mode": "full_page", "confidence_threshold": 0.5,
                      "zones": [{"name": "invoice_no", "regex": r"No:\s*(\S+)"}]}
    channel = channel or LocalFolderChannel(profile["delivery"]["local_folder"])
    pipeline = Pipeline(db, profile, [channel], export_document, audit=audit,
                        working_dir=tmp_path / "staging",
                        ocr_fn=lambda image: OCRResult("Invoice No: INV-4242", 0.95))
    jobs = write_pages(tmp_path, ["text"] * pages)
    for j in jobs:
        j.batch_id = "batch-1"
    pipeline.start()
    try:
        pipeline.submit_batch("batch-1", jobs, operator="admin")
        assert wait_for(lambda: any(d["delivery_status"] != "pending"
                                    for d in db.list_documents("batch-1")))
    finally:
        if not keep:
            pipeline.shutdown()
    return db.list_documents("batch-1")[0], tmp_path, pipeline


def actions(db):
    return [r["action"] for r in db.conn.execute("select action from audit_log")]


def parse_csv(path):
    return list(csv.DictReader(io.StringIO(path.read_text(encoding="utf-8-sig"))))


def test_without_the_setting_no_index_file_is_written(env):
    doc, tmp, _p = run(env, None)
    assert not list((tmp / "exports").glob("*.csv"))
    assert not list((tmp / "delivered").glob("*.csv"))


def test_a_csv_index_is_written_next_to_the_pdf_and_delivered_with_it(env):
    doc, tmp, _p = run(env, {"enabled": True, "format": "csv"})
    pdf = list((tmp / "exports").glob("*.pdf"))[0]
    row = parse_csv(pdf.with_suffix(".csv"))[0]
    assert row["invoice_no"] == "INV-4242" and row["page_count"] == "2"
    assert row["operator"] == "admin" and row["project"] == "Test Project"
    assert row["document_id"] == doc["document_id"] and row["pdf_file"] == pdf.name
    delivered = {p.name for p in (tmp / "delivered").iterdir()}
    assert delivered == {pdf.name, pdf.with_suffix(".csv").name}
    detail = json.loads(doc["delivery_detail"])["local_folder"]
    assert detail["success"] and detail["index"]["success"]


@pytest.mark.parametrize("fmt,ext", [("xml", ".xml"), ("json", ".json")])
def test_xml_and_json_indexes(env, fmt, ext):
    doc, tmp, _p = run(env, {"enabled": True, "format": fmt})
    pdf = list((tmp / "exports").glob("*.pdf"))[0]
    text = pdf.with_suffix(ext).read_text(encoding="utf-8")
    if fmt == "xml":
        assert ET.fromstring(text.encode("utf-8")).findtext("invoice_no") == "INV-4242"
    else:
        assert json.loads(text)["invoice_no"] == "INV-4242"
    assert (tmp / "delivered" / pdf.with_suffix(ext).name).exists()


def test_the_running_index_collects_every_document(env):
    doc, tmp, _p = run(env, {"enabled": True, "format": "xml", "running_csv": True})
    rows = parse_csv(tmp / "exports" / "index.csv")
    assert [r["document_id"] for r in rows] == [doc["document_id"]]
    assert rows[0]["invoice_no"] == "INV-4242"


def test_the_index_works_with_other_export_formats_too(env):
    doc, tmp, _p = run(env, {"enabled": True, "format": "csv"}, fmt="tiff")
    tif = list((tmp / "exports").glob("*.tif"))[0]
    assert tif.with_suffix(".csv").exists()


class IndexRefusingChannel(LocalFolderChannel):
    """Accepts the PDF, refuses the index file."""

    def deliver(self, doc_path, metadata):
        if str(doc_path).endswith(".csv"):
            return DeliveryResult(success=False, channel=self.name,
                                  detail={"error": "refused"})
        return super().deliver(doc_path, metadata)


def test_an_index_that_cannot_be_delivered_never_fails_the_document(env):
    db, audit, tmp = env
    profile = make_profile(tmp)
    channel = IndexRefusingChannel(profile["delivery"]["local_folder"])
    doc, tmp, _p = run(env, {"enabled": True, "format": "csv"}, channel=channel)
    assert doc["delivery_status"] == "delivered_local"          # the PDF went
    detail = json.loads(doc["delivery_detail"])["local_folder"]
    assert detail["success"] and detail["index"]["success"] is False
    assert "index_not_delivered" in actions(db)


def test_a_failure_writing_the_index_is_audited_by_type_and_the_pdf_still_goes(env, monkeypatch):
    db, audit, tmp = env

    def broken(*a, **k):
        raise PermissionError("S3CRET-VALUE-123 cannot be written")
    monkeypatch.setattr("pipeline.job_queue.write_index", broken)
    doc, tmp, _p = run(env, {"enabled": True, "format": "csv"})
    assert doc["delivery_status"] == "delivered_local"
    events = db.conn.execute(
        "select detail from audit_log where action='index_failed'").fetchall()
    assert len(events) == 1
    assert "PermissionError" in events[0][0] and "S3CRET" not in events[0][0]
    assert not list((tmp / "delivered").glob("*.csv"))


def test_values_that_look_like_formulas_are_neutralised_in_the_delivered_file(env):
    db, audit, tmp = env
    profile = make_profile(tmp)
    profile["separation"] = {"strategy": ["fixed_pages"], "fixed_pages": 1,
                             "blank_threshold": 0.02}
    profile["export"]["index"] = {"enabled": True, "format": "csv"}
    profile["features"]["auto_index"] = True
    profile["ocr"] = {"lang": "eng", "mode": "full_page", "confidence_threshold": 0.5,
                      "zones": [{"name": "memo", "regex": r"memo\s+(\S+)"}]}
    pipeline = Pipeline(db, profile,
                        [LocalFolderChannel(profile["delivery"]["local_folder"])],
                        export_document, working_dir=tmp / "staging",
                        ocr_fn=lambda i: OCRResult("memo =HYPERLINK(1)", 0.95))
    jobs = write_pages(tmp, ["text"])
    for j in jobs:
        j.batch_id = "batch-f"
    pipeline.start()
    try:
        pipeline.submit_batch("batch-f", jobs)
        assert wait_for(lambda: any(d["delivery_status"] != "pending"
                                    for d in db.list_documents("batch-f")))
    finally:
        pipeline.shutdown()
    pdf = list((tmp / "exports").glob("*.pdf"))[0]
    assert parse_csv(pdf.with_suffix(".csv"))[0]["memo"] == "'=HYPERLINK(1)"



# -- a correction after export reaches the bank ---------------------------------------------------
def corrected_fields(db, doc):
    fields = json.loads(doc["extracted_fields"])
    fields["invoice_no"] = "INV-CORRECTED"
    db.update_document(doc["document_id"], extracted_fields=fields)


def test_correcting_a_field_rewrites_and_redelivers_the_index_file(env):
    db, audit, tmp = env
    doc, tmp, pipeline = run(env, {"enabled": True, "format": "csv",
                                   "running_csv": True}, keep=True)
    try:
        corrected_fields(db, doc)
        assert pipeline.reissue_index(doc["document_id"]) is True
        pdf = list((tmp / "exports").glob("*.pdf"))[0]
        delivered = tmp / "delivered" / pdf.with_suffix(".csv").name
        assert wait_for(lambda: parse_csv(delivered)[0]["invoice_no"] == "INV-CORRECTED")
        assert parse_csv(pdf.with_suffix(".csv"))[0]["invoice_no"] == "INV-CORRECTED"
        running = parse_csv(tmp / "exports" / "index.csv")
        assert len(running) == 1 and running[0]["invoice_no"] == "INV-CORRECTED"
        assert wait_for(lambda: "index_reissued" in actions(db))
    finally:
        pipeline.shutdown()


def test_nothing_is_reissued_when_the_project_has_no_index_file(env):
    db, audit, tmp = env
    doc, tmp, pipeline = run(env, None, keep=True)
    try:
        assert pipeline.reissue_index(doc["document_id"]) is False
    finally:
        pipeline.shutdown()


def test_a_document_that_was_never_exported_is_not_reissued(env):
    db, audit, tmp = env
    doc, tmp, pipeline = run(env, {"enabled": True, "format": "csv"}, keep=True)
    try:
        db.update_document(doc["document_id"], storage_path="")
        assert pipeline.reissue_index(doc["document_id"]) is False
        assert pipeline.reissue_index("no-such-document") is False
    finally:
        pipeline.shutdown()


def test_the_index_tab_save_triggers_the_reissue(qtbot, tmp_path):
    """MainWindow wires a saved correction to the pipeline."""
    from ui.event_bus import EventBus
    from projects.profile_manager import ProfileManager
    from security.rbac import Session, User
    from ui.main_window import MainWindow
    seen = []

    class FakePipeline:
        refused_batches = []

        def reissue_index(self, document_id):
            seen.append(document_id)
            return True

        def shutdown(self, wait=True):
            pass

    db = Database(tmp_path / "w.db")
    audit = AuditLogger(db, tmp_path / "logs")
    try:
        ProfileManager(tmp_path / "projects", db).create("P")
        Session._current = User("a1", "root", "admin")
        win = MainWindow(db, ProfileManager(tmp_path / "projects", db), EventBus(),
                         pipeline=FakePipeline(), audit=audit,
                         staging_dir=tmp_path / "staging")
        qtbot.addWidget(win)
        win.index_editor.fields_saved.emit("doc-9", {"a": "1"})
        assert seen == ["doc-9"]
    finally:
        Session.logout()
        audit.close()
        db.close()
