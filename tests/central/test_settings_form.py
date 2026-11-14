"""The project settings form (central/dashboard/form.js), tested with node: the
values a config shows, what is wrong with them, and a config rebuilt from them."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

FORM_JS = Path(__file__).parents[2] / "central" / "dashboard" / "form.js"
node = shutil.which("node")
pytestmark = pytest.mark.skipif(node is None, reason="node is not installed")


def run(expression: str, **data):
    script = ("const F = require(process.argv[1]); "
              "const d = JSON.parse(process.argv[2]); "
              f"console.log(JSON.stringify({expression}));")
    out = subprocess.run([node, "-e", script, str(FORM_JS), json.dumps(data)],
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


CONFIG = {
    "project_id": "p1", "name": "Bank", "version": 3,
    "scanner": {"dpi": 300, "hot_folder": {"enabled": True, "path": "D:/inbox"}},
    "export": {"output_path": "D:/out"}, "delivery": {"local_folder": {"path": "D:/d"}},
    "qc": {"enabled": True, "pass_score": 85, "dpi_warn": 200, "stages": ["rotation"]},
    "ocr": {"lang": "eng", "mode": "zone", "confidence_threshold": 0.75,
            "zones": [{"name": "nic", "x": 10, "y": 20, "w": 30, "h": 5, "ref_w": 100, "ref_h": 200,
                       "comb": True, "regex": "(\\d{12})"},
                      {"name": "name", "x": 1, "y": 2, "w": 3, "h": 4}],
            "document_types": [{"name": "NIC", "regex": "identity"}]},
    "separation": {"strategy": ["barcode", "document_type"], "barcode_role": "auto",
                   "barcode_pattern": "^CIF-", "drop_blank_pages": True, "fixed_pages": None},
    "features": {"auto_index": True, "duplicate_check": False, "sftp": True},
}


def form_of(config):
    return run("F.toForm(d.c)", c=config)


def test_the_form_shows_the_configs_values_with_sensible_defaults():
    f = form_of(CONFIG)
    assert f["ocr_lang"] == "eng" and f["ocr_mode"] == "zone" and f["ocr_confidence"] == 0.75
    assert f["strategies"] == ["barcode", "document_type"] and f["barcode_role"] == "auto"
    assert f["barcode_pattern"] == "^CIF-" and f["drop_blank_pages"] is True
    assert [r["name"] for r in f["fields"]] == ["nic", "name"] and f["document_types"] == 1
    assert f["auto_index"] is True and f["duplicate_check"] is False
    empty = form_of({})
    assert empty["ocr_lang"] == "eng" and empty["qc_pass_score"] == 85 and empty["fields"] == []


def test_applying_the_form_changes_only_what_the_form_covers():
    f = form_of(CONFIG)
    f.update(ocr_confidence="0.8", qc_pass_score="90", strategies=["blank_page"],
             barcode_pattern="", drop_blank_pages=False, auto_index=False)
    out = run("F.applyForm(d.c, d.f)", c=CONFIG, f=f)
    assert out["ocr"]["confidence_threshold"] == 0.8 and out["qc"]["pass_score"] == 90
    assert out["separation"]["strategy"] == ["blank_page"]
    assert "barcode_pattern" not in out["separation"] and "drop_blank_pages" not in out["separation"]
    assert out["features"]["auto_index"] is False and out["features"]["sftp"] is True   # untouched
    # everything the form does not show is kept exactly
    for key in ("scanner", "export", "delivery", "project_id", "name", "version"):
        assert out[key] == CONFIG[key]
    assert out["ocr"]["document_types"] == CONFIG["ocr"]["document_types"]
    assert out["qc"]["stages"] == ["rotation"]
    assert CONFIG["ocr"]["confidence_threshold"] == 0.75                                # input not mutated


def test_index_fields_keep_their_boxes_when_renamed_or_re_patterned():
    f = form_of(CONFIG)
    f["fields"][0]["name"] = "nic_number"
    f["fields"][0]["regex"] = ""
    f["fields"][1]["regex"] = "(\\w+)"
    f["fields"].append({"i": -1, "name": "branch", "regex": ""})
    zones = run("F.applyForm(d.c, d.f).ocr.zones", c=CONFIG, f=f)
    assert zones[0] == {"name": "nic_number", "x": 10, "y": 20, "w": 30, "h": 5,
                        "ref_w": 100, "ref_h": 200, "comb": True}
    assert zones[1]["regex"] == "(\\w+)" and zones[1]["x"] == 1
    assert zones[2] == {"name": "branch"}


def test_a_removed_or_unnamed_field_is_dropped():
    f = form_of(CONFIG)
    f["fields"].pop(0)
    f["fields"].append({"i": -1, "name": "   ", "regex": "x"})
    assert [z["name"] for z in run("F.applyForm(d.c, d.f).ocr.zones", c=CONFIG, f=f)] == ["name"]


def test_strategies_follow_a_fixed_order_and_fixed_pages_is_a_whole_number():
    f = form_of(CONFIG)
    f.update(strategies=["fixed_pages", "barcode"], fixed_pages="3")
    out = run("F.applyForm(d.c, d.f).separation", c=CONFIG, f=f)
    assert out["strategy"] == ["barcode", "fixed_pages"] and out["fixed_pages"] == 3
    f.update(strategies=["barcode"], fixed_pages="")
    assert run("F.applyForm(d.c, d.f).separation.fixed_pages", c=CONFIG, f=f) is None


@pytest.mark.parametrize("change,expected", [
    ({"ocr_confidence": "1.5"}, "OCR confidence"),
    ({"qc_pass_score": "120"}, "pass score"),
    ({"qc_blur_warn": "abc"}, "blur warning"),
    ({"strategies": ["fixed_pages"], "fixed_pages": ""}, "pages each document"),
])
def test_wrong_values_are_explained(change, expected):
    f = form_of(CONFIG)
    f.update(change)
    problems = run("F.validate(d.f)", f=f)
    assert any(expected in p for p in problems), problems


def test_duplicate_and_blank_field_names_are_explained():
    f = form_of(CONFIG)
    f["fields"][1]["name"] = "NIC"
    f["fields"].append({"i": -1, "name": "", "regex": ""})
    problems = run("F.validate(d.f)", f=f)
    assert any("appears twice" in p for p in problems) and any("needs a name" in p for p in problems)
    assert run("F.validate(d.f)", f=form_of(CONFIG)) == []


def test_a_form_made_config_passes_the_projects_own_schema(tmp_path):
    from projects.profile_manager import ProfileManager
    base = ProfileManager(tmp_path / "pm").create("Seed")
    f = form_of(base)
    f.update(strategies=["barcode", "blank_page"], barcode_role="cover_sheet",
             barcode_pattern="^CIF-\\d+$", qc_pass_score="80", ocr_confidence="0.6")
    out = run("F.applyForm(d.c, d.f)", c=base, f=f)
    ProfileManager.validate(out)                         # the same check the server applies
    assert out["separation"]["strategy"] == ["barcode", "blank_page"]
