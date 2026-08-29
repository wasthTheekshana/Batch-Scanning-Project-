"""Tests for the project-templates system (Enhancement Plan §4.4)."""
import json

import pytest

from data.db import Database


@pytest.fixture()
def db(tmp_path):
    d = Database(tmp_path / "templates.db")
    yield d
    d.close()


def test_insert_and_get_template(db):
    db.insert_template("t1", "Banking", "Banking", {"ocr": {"zones": []}})
    row = db.get_template("t1")
    assert row["name"] == "Banking"
    assert row["category"] == "Banking"
    assert json.loads(row["config_json"]) == {"ocr": {"zones": []}}
    assert row["built_in"] == 0


def test_insert_template_built_in_flag(db):
    db.insert_template("t1", "Banking", "Banking", {}, built_in=True)
    assert db.get_template("t1")["built_in"] == 1


def test_list_templates_ordered_by_category_then_name(db):
    db.insert_template("t1", "Zeta Corp", "Legal", {})
    db.insert_template("t2", "Alpha Bank", "Banking", {})
    db.insert_template("t3", "Beta Co", "Banking", {})
    rows = db.list_templates()
    assert [(r["category"], r["name"]) for r in rows] == [
        ("Banking", "Alpha Bank"), ("Banking", "Beta Co"),
        ("Legal", "Zeta Corp")]


def test_update_template_config(db):
    db.insert_template("t1", "Banking", "Banking", {"a": 1})
    db.update_template_config("t1", {"a": 2})
    assert json.loads(db.get_template("t1")["config_json"]) == {"a": 2}


def test_get_template_missing_returns_none(db):
    assert db.get_template("no-such-id") is None
