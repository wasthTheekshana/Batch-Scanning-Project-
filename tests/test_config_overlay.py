import copy

import pytest

from projects.config_overlay import (LOCAL_ONLY_KEYS, PulledProjectError,
                                 default_local_sections, overlay,
                                 validate_entry)
from projects.profile_manager import DEFAULT_PROJECT

PID = "11111111-2222-3333-4444-555555555555"


def entry(**over):
    cfg = copy.deepcopy(DEFAULT_PROJECT)
    cfg.update(project_id=PID, name="Invoices")
    cfg["ocr"]["lang"] = "deu"
    e = {"project_id": PID, "name": "Invoices", "version": 3, "config": cfg}
    e.update(over)
    return e


# ---- validate_entry -----------------------------------------------------------
def test_valid_entry_passes():
    validate_entry(entry())


UNSAFE_IDS = ["../evil", "a/b", "a\\b", "", "x" * 37, "has space", "..", "a.b",
              5, None, "abc\n", 123, ["a"], "CON", "nul", "Com1", "LPT9"]


@pytest.mark.parametrize("bad_id", UNSAFE_IDS)
def test_unsafe_ids_rejected(bad_id):
    e = entry(project_id=bad_id)
    e["config"]["project_id"] = bad_id  # so only the id check can reject it
    with pytest.raises(PulledProjectError, match="unsafe project_id"):
        validate_entry(e)


@pytest.mark.parametrize("bad_version", [0, -1, "2", 1.5, True, None])
def test_bad_versions_rejected(bad_version):
    with pytest.raises(PulledProjectError):
        validate_entry(entry(version=bad_version))


@pytest.mark.parametrize("field,value", [("name", ""), ("name", 5),
                                         ("config", "x"), ("config", None)])
def test_bad_name_or_config_rejected(field, value):
    with pytest.raises(PulledProjectError):
        validate_entry(entry(**{field: value}))


def test_config_project_id_must_match_entry_id():
    e = entry()
    e["config"]["project_id"] = "someone-else"
    with pytest.raises(PulledProjectError):
        validate_entry(e)


def test_non_dict_entry_rejected():
    with pytest.raises(PulledProjectError):
        validate_entry("nope")


# ---- default_local_sections ----------------------------------------------------
def test_default_local_sections_are_seeded_with_local_paths(tmp_path):
    d = default_local_sections(tmp_path)
    assert set(d) == set(LOCAL_ONLY_KEYS)
    assert d["export"]["output_path"] == str(tmp_path / "output")
    assert d["delivery"]["local_folder"]["path"] == str(tmp_path / "delivered")
    assert d["delivery"]["sftp"].get("enabled") is False
    d2 = default_local_sections(tmp_path)
    d["export"]["output_path"] = "mutated"
    assert d2["export"]["output_path"] != "mutated"  # fresh copy each call


# ---- overlay ---------------------------------------------------------------------
def test_first_pull_seeds_local_sections(tmp_path):
    defaults = default_local_sections(tmp_path)
    cfg = overlay(entry(), None, defaults)
    assert cfg["project_id"] == PID and cfg["name"] == "Invoices"
    assert cfg["ocr"]["lang"] == "deu"                    # central content applied
    assert cfg["export"]["output_path"] == str(tmp_path / "output")
    assert cfg["delivery"] == defaults["delivery"]


def test_update_keeps_local_sections_untouched(tmp_path):
    local = copy.deepcopy(DEFAULT_PROJECT)
    local["scanner"] = {"dpi": 600, "color": "color", "duplex": False, "sdk": "twain"}
    local["export"]["output_path"] = "D:/scans/out"
    local["delivery"]["sftp"] = {"enabled": True, "host": "10.0.0.5",
                                 "username": "svc", "password": "local-secret"}
    cfg = overlay(entry(), local, default_local_sections(tmp_path))
    assert cfg["scanner"] == local["scanner"]
    assert cfg["export"] == local["export"]
    assert cfg["delivery"] == local["delivery"]
    assert cfg["ocr"]["lang"] == "deu"


def test_central_can_never_supply_local_sections(tmp_path):
    e = entry()
    e["config"]["delivery"]["sftp"] = {"enabled": True, "host": "evil.example"}
    e["config"]["export"]["output_path"] = "\\\\evil\\share"
    e["config"]["scanner"] = {"dpi": 1, "color": "x", "duplex": True, "sdk": "evil"}
    cfg = overlay(e, None, default_local_sections(tmp_path))
    assert cfg["delivery"]["sftp"].get("host") is None
    assert "evil" not in cfg["export"]["output_path"]
    assert cfg["scanner"]["sdk"] != "evil"


def test_overlay_does_not_mutate_inputs(tmp_path):
    e, local = entry(), copy.deepcopy(DEFAULT_PROJECT)
    e0, l0 = copy.deepcopy(e), copy.deepcopy(local)
    overlay(e, local, default_local_sections(tmp_path))
    assert e == e0 and local == l0


def test_entry_name_and_id_win_over_config_values(tmp_path):
    e = entry()
    e["config"]["name"] = "stale"
    cfg = overlay(e, None, default_local_sections(tmp_path))
    assert cfg["name"] == "Invoices" and cfg["project_id"] == PID


# ---- controller rulings --------------------------------------------------------
@pytest.mark.parametrize("good_id", ["x" * 36, "console", "com10"])
def test_boundary_ids_accepted(good_id):
    e = entry(project_id=good_id)
    e["config"]["project_id"] = good_id
    validate_entry(e)


def test_unknown_top_level_keys_pass_through_untouched(tmp_path):
    e = entry()
    e["config"]["future_section"] = {"a": [1, 2, {"b": 3}]}
    cfg = overlay(e, None, default_local_sections(tmp_path))
    assert cfg["future_section"] == {"a": [1, 2, {"b": 3}]}


def test_local_missing_a_section_falls_back_to_defaults(tmp_path):
    local = copy.deepcopy(DEFAULT_PROJECT)
    del local["delivery"]
    local["export"]["output_path"] = "D:/scans/out"
    defaults = default_local_sections(tmp_path)
    cfg = overlay(entry(), local, defaults)
    assert cfg["delivery"] == defaults["delivery"]
    assert cfg["export"]["output_path"] == "D:/scans/out"


def test_config_without_project_id_passes_and_overlay_stamps_entry_id(tmp_path):
    e = entry()
    del e["config"]["project_id"]
    validate_entry(e)
    cfg = overlay(e, None, default_local_sections(tmp_path))
    assert cfg["project_id"] == PID


# ---- rule actions that could escape the output root ------------------------------
def _with_action(action):
    e = entry()
    e["config"]["rules"]["rules"] = [
        {"rule_id": "r1", "actions": [{"type": "add_flag", "value": "x"}, action]}]
    return e


@pytest.mark.parametrize("value", ["../../x", "D:/evil", "\\\\h\\s", "/abs",
                                   "a/../../b", "C:evil", "CON", 5, None])
def test_unsafe_set_folder_rejected_naming_the_rule(value):
    with pytest.raises(PulledProjectError, match="set_folder.*rule 0 action 1"):
        validate_entry(_with_action({"type": "set_folder", "value": value}))


@pytest.mark.parametrize("value", ["a/b", "a\\b", "..", 5, None, "", "name.",
                                   "name ", "CON", "nul.txt", "Com1",
                                   "LPT9.pdf", "a\x00b"])
def test_unsafe_set_filename_rejected(value):
    with pytest.raises(PulledProjectError, match="set_filename"):
        validate_entry(_with_action({"type": "set_filename", "value": value}))


@pytest.mark.parametrize("action", [
    {"type": "set_folder", "value": "Exceptions/Missing"},
    {"type": "set_folder", "value": ""},
    {"type": "set_filename", "value": "{date}_{counter:04d}"},
    {"type": "set_filename", "value": "console_{counter}"},
    {"type": "set_validation_status", "value": "rejected"}])
def test_clean_rule_actions_pass(action):
    validate_entry(_with_action(action))


@pytest.mark.parametrize("rules", [None, "x", {"rules": "x"}, {"rules": [5]},
                                   {"rules": [{"actions": "x"}]},
                                   {"rules": [{"actions": [7]}]}])
def test_odd_rule_shapes_never_raise_anything_but_pulled_error(rules):
    e = entry()
    e["config"]["rules"] = rules
    validate_entry(e)  # odd shapes are the schema's problem, not a path escape
