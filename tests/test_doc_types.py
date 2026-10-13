from ocr.doc_types import (all_zones, classify_page, document_types,
                           inherit_types, page_zones, type_runs)

NIC = {"name": "NIC", "regex": "national identity card",
       "zones": [{"name": "nic_number", "regex": r"(\d{12})"}]}
APP = {"name": "Application", "regex": r"application\s+form",
       "zones": [{"name": "application_number", "regex": r"(APP-\d+)"},
                 {"name": "customer_name", "regex": r"Name:\s*(\w+)"}]}
OCR = {"zones": [{"name": "branch", "regex": r"Branch:\s*(\w+)"}],
       "document_types": [NIC, APP]}


def test_the_first_matching_type_wins_and_matching_ignores_case():
    assert classify_page("NATIONAL IDENTITY CARD ... APPLICATION FORM", OCR) is NIC
    assert classify_page("Loan Application Form", OCR) is APP


def test_no_match_means_no_type():
    assert classify_page("random letter text", OCR) is None
    assert classify_page("", OCR) is None
    assert classify_page("anything", {"zones": []}) is None


def test_a_type_without_a_pattern_or_with_a_broken_pattern_never_matches():
    ocr = {"document_types": [{"name": "A", "zones": []},
                              {"name": "B", "regex": "(", "zones": []},
                              {"name": "C", "regex": "letter", "zones": []}]}
    assert classify_page("a letter", ocr)["name"] == "C"


def test_a_page_gets_the_common_zones_plus_its_types_zones():
    zones, name = page_zones(OCR, "Application Form  Branch: Fort")
    assert name == "Application"
    assert [z["name"] for z in zones] == ["branch", "application_number",
                                          "customer_name"]


def test_an_unclassified_page_gets_only_the_common_zones():
    zones, name = page_zones(OCR, "some letter")
    assert name is None
    assert [z["name"] for z in zones] == ["branch"]


def test_a_type_zone_replaces_a_common_zone_of_the_same_name():
    ocr = {"zones": [{"name": "ref", "regex": "common"}],
           "document_types": [{"name": "T", "regex": "x",
                               "zones": [{"name": "ref", "regex": "own"}]}]}
    zones, _ = page_zones(ocr, "x")
    assert zones == [{"name": "ref", "regex": "own"}]


def test_all_zones_is_every_field_once_for_the_index_form():
    ocr = {"zones": [{"name": "branch", "regex": "a"}],
           "document_types": [NIC, {"name": "X", "regex": "x",
                                    "zones": [{"name": "branch", "regex": "dup"},
                                              {"name": "other", "regex": "o"}]}]}
    zones = all_zones(ocr)
    assert [z["name"] for z in zones] == ["branch", "nic_number", "other"]
    assert zones[0]["regex"] == "a"             # first definition wins


def test_projects_without_types_are_unchanged():
    ocr = {"zones": [{"name": "a", "regex": "x"}]}
    assert document_types(ocr) == []
    assert all_zones(ocr) == ocr["zones"]
    assert page_zones(ocr, "text") == (ocr["zones"], None)


def test_pages_without_a_type_inherit_the_previous_pages_type():
    assert inherit_types([None, "NIC", None, "Application", None, None]) == \
        [None, "NIC", "NIC", "Application", "Application", "Application"]


def test_runs_give_one_bookmark_per_stretch_of_one_type():
    assert type_runs([None, "NIC", "NIC", "Application", "Application",
                      "NIC"]) == [(1, "NIC"), (3, "Application"), (5, "NIC")]
    assert type_runs([None, None]) == []
