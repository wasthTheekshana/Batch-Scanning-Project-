"""A document type can ask for its own image clean-up (a handwritten form wants a gentler one
than a printed invoice)."""
import json

import pytest

import core.job_queue as job_queue
from data.db import Database
from imaging.enhance import ENHANCEMENT_PRESETS, preset_name
from ocr.doc_types import enhancement_for
from tests.test_pipeline import build_pipeline, make_profile, wait_for, write_pages

PROJECT = {"deskew": True, "crop": True, "clahe": True, "despeckle": True,
           "whiten_bg": True, "target_dpi": 300}
GENTLE = ENHANCEMENT_PRESETS["gentle"][1]


def cfg(**extra):
    return {"document_types": [
        {"name": "Handwritten form", "regex": "form", "enhancement_profile": GENTLE, **extra},
        {"name": "Invoice", "regex": "invoice"}]}


def test_a_type_overrides_the_projects_settings_and_the_rest_stay():
    merged = enhancement_for(cfg(), PROJECT, "Handwritten form")
    assert merged["deskew"] and merged["crop"]                          # kept (gentle keeps them on)
    assert not (merged["whiten_bg"] or merged["clahe"] or merged["despeckle"])
    assert merged["target_dpi"] == 300


def test_a_type_without_overrides_and_a_page_without_a_type_use_the_project(monkeypatch):
    assert enhancement_for(cfg(), PROJECT, "Invoice") == PROJECT
    assert enhancement_for(cfg(), PROJECT, None) == PROJECT
    assert enhancement_for(cfg(), PROJECT, "Unknown type") == PROJECT
    assert enhancement_for({}, PROJECT, "Invoice") == PROJECT


def test_the_projects_resolution_cannot_be_changed_by_a_type():
    odd = cfg(enhancement_profile={"target_dpi": 75, "deskew": False})
    merged = enhancement_for(odd, PROJECT, "Handwritten form")
    assert merged["target_dpi"] == 300 and merged["deskew"] is False


def test_presets_round_trip_through_their_stored_form():
    for key, (_label, values) in ENHANCEMENT_PRESETS.items():
        assert preset_name(values or None) == key
    assert preset_name({"deskew": True}) == "project"                  # partial or unknown: project


def test_the_schema_accepts_the_override_and_rejects_nonsense():
    import jsonschema
    schema = json.load(open("core/project_schema.json", encoding="utf-8"))
    item = schema["properties"]["ocr"]["properties"]["document_types"]["items"]
    jsonschema.validate({"name": "T", "enhancement_profile": GENTLE}, item)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"name": "T", "enhancement_profile": {"deskew": "yes"}}, item)


def test_the_document_type_dialog_offers_and_stores_the_choice(qtbot):
    from ui.document_type_dialog import DocumentTypeDialog
    dlg = DocumentTypeDialog({"name": "Form", "regex": "form", "zones": [{"name": "a", "regex": "x"}],
                              "enhancement_profile": GENTLE})
    qtbot.addWidget(dlg)
    assert dlg.enhancement.currentData() == "gentle"                    # an existing choice is shown
    assert dlg.build_type()["enhancement_profile"] == GENTLE
    dlg.enhancement.setCurrentIndex(dlg.enhancement.findData("project"))
    assert "enhancement_profile" not in dlg.build_type()                # "same as project" stores nothing


def test_the_bank_forms_default_to_the_gentle_setting():
    from pathlib import Path
    template = json.loads((Path(__file__).parent.parent / "core" / "templates"
                           / "bank_customer_file.json").read_text(encoding="utf-8"))
    types = {t["name"]: t for t in template["config"]["ocr"]["document_types"]}
    for name in ("Credit card application", "Personal loan application",
                 "Education loan application", "Home loan application"):
        assert types[name]["enhancement_profile"] == GENTLE, name
    assert "enhancement_profile" not in types["Letter"]


# -- through the real pipeline ------------------------------------------------------------------
def test_the_pipeline_uses_the_pages_document_type_for_its_clean_up(tmp_path, monkeypatch):
    from ocr.tesseract_engine import OCRResult
    seen = []
    real = job_queue.apply_profile
    monkeypatch.setattr(job_queue, "apply_profile",
                        lambda img, profile, dpi=300: (seen.append(dict(profile)), real(img, profile, dpi))[1])
    profile = make_profile(tmp_path)
    profile["enhancement_profile"] = dict(PROJECT)
    profile["ocr"] = {"lang": "eng", "zones": [], "confidence_threshold": 0.75, **cfg()}
    db = Database(tmp_path / "e.db")
    pipeline = build_pipeline(tmp_path, db, profile)
    pipeline.ocr_fn = lambda image: OCRResult(text="loan application form", confidence=0.9, words=[])
    pipeline.start()
    try:
        pipeline.submit_batch("batch-1", write_pages(tmp_path, ["text"]), operator="op1")
        assert wait_for(lambda: seen)
    finally:
        pipeline.shutdown()
        db.close()
    assert seen[0]["whiten_bg"] is False and seen[0]["clahe"] is False      # the form's own settings
