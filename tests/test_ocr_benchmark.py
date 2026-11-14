"""The OCR benchmark's scoring and test data (the full run is a script)."""
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import ocr_benchmark as bm  # noqa: E402

from pipeline.validation import validate  # noqa: E402


def test_edit_distance_and_character_accuracy():
    assert bm.edit_distance("kitten", "sitting") == 3
    assert bm.edit_distance("", "abc") == 3 and bm.edit_distance("abc", "abc") == 0
    assert bm.char_accuracy("12345", "12345") == 1.0
    assert bm.char_accuracy("12345", "12945") == pytest.approx(0.8)
    assert bm.char_accuracy("12", "") == 0.0 and bm.char_accuracy("", "") == 1.0


def test_the_tally_counts_exact_matches_per_group_and_field():
    t = bm.Tally()
    t.add("clean", "nic", "199012345678", "199012345678")
    t.add("clean", "nic", "199012345678", "199012345679")
    t.add("noisy", "nic", "199012345678", "")
    t.add("clean", "name", "A B", " A B ")                    # surrounding blanks do not count
    assert t.exact("clean", "nic") == 0.5 and t.exact("noisy", "nic") == 0.0
    assert t.exact("clean") == pytest.approx(2 / 3)
    assert t.exact() == pytest.approx(0.5)
    assert t.groups() == ["clean", "noisy"] and t.fields() == ["nic", "name"]
    assert t.exact("nope") is None


def test_the_report_has_a_row_per_field_and_says_typed_text():
    t = bm.Tally()
    t.add("clean", "nic", "1", "1")
    text = bm.report(t, "Title", "typed note")
    assert "# Title" in text and "| nic |" in text and "typed note" in text
    assert "**All fields**" in text and "100.0%" in text


def test_random_customers_are_well_formed():
    rng = random.Random(7)
    for _ in range(200):
        c = bm.customer(rng)
        assert validate("nic", c["nic_number"]) is None, c["nic_number"]
        assert validate("date", c["date_of_birth"]) is None
        assert validate("phone", c["mobile"]) is None and validate("email", c["email"]) is None
        assert len(c["account_number"]) == 10 and set(c) == {n for n, *_ in bm.loan_form.FIELDS}


def test_the_same_seed_gives_the_same_customers():
    a, b = random.Random(3), random.Random(3)
    assert [bm.customer(a) for _ in range(5)] == [bm.customer(b) for _ in range(5)]


def test_a_real_scan_folder_is_scored_from_its_truth_files(tmp_path, monkeypatch):
    import json

    import cv2
    import numpy as np
    cv2.imwrite(str(tmp_path / "a.png"), np.full((50, 50), 255, np.uint8))
    (tmp_path / "a.json").write_text(json.dumps({"nic_number": "199012345678", "mobile": "0771234567"}))
    (tmp_path / "orphan.json").write_text("{}")                # no scan beside it
    monkeypatch.setattr(bm, "read_page", lambda page: {"nic_number": "199012345678", "mobile": "0771234560"})
    t = bm.run_real(tmp_path, progress=lambda *_: None)
    assert t.exact(field="nic_number") == 1.0 and t.exact(field="mobile") == 0.0
    assert t.groups() == ["real scans"]
