from ocr.doc_types import (all_zones, classify_page, document_types,
                           inherit_types, match_page, page_zone_groups,
                           page_zones, type_runs)

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


def names(groups):
    return [([z["name"] for z in zones], mode) for zones, mode in groups]


def test_groups_read_common_zones_by_the_project_mode_and_types_by_their_own():
    form = {"name": "Form", "regex": "loan form", "mode": "zone",
            "zones": [{"name": "account", "x": 1, "y": 2, "w": 3, "h": 4}]}
    ocr = {"mode": "full_page", "zones": [{"name": "branch", "regex": "b"}],
           "document_types": [form, NIC]}
    groups, name = page_zone_groups(ocr, "the loan form")
    assert name == "Form"
    assert names(groups) == [(["branch"], "full_page"), (["account"], "zone")]
    groups, name = page_zone_groups(ocr, "NATIONAL IDENTITY CARD")
    assert names(groups) == [(["branch"], "full_page"), (["nic_number"], "full_page")]


def test_a_type_without_a_mode_follows_the_project():
    ocr = {"mode": "zone", "document_types": [NIC]}
    assert names(page_zone_groups(ocr, "national identity card")[0]) == [
        (["nic_number"], "zone")]
    assert names(page_zone_groups({"document_types": [NIC]},
                                  "national identity card")[0]) == [
        (["nic_number"], "zone")]            # no mode anywhere: zone, like the pipeline


def test_unclassified_pages_get_just_the_common_zones_and_none_means_no_groups():
    ocr = {"mode": "full_page", "zones": [{"name": "branch", "regex": "b"}],
           "document_types": [NIC]}
    assert names(page_zone_groups(ocr, "letter")[0]) == [(["branch"], "full_page")]
    assert page_zone_groups({"mode": "zone", "document_types": [NIC]}, "letter") == ([], None)


def test_a_type_zone_replaces_the_common_zone_of_the_same_name_in_groups():
    ocr = {"mode": "full_page", "zones": [{"name": "ref", "regex": "c"}],
           "document_types": [{"name": "T", "regex": "x", "mode": "zone",
                               "zones": [{"name": "ref", "x": 0, "y": 0, "w": 1, "h": 1}]}]}
    groups, _ = page_zone_groups(ocr, "x")
    assert names(groups) == [(["ref"], "zone")]


FORM = {"name": "Form", "regex": "loan application", "continuation_regex": "course details",
        "mode": "zone", "zones": [{"name": "account", "x": 1, "y": 2, "w": 3, "h": 4}]}
GENERIC = {"name": "Generic", "regex": "application form", "zones": [{"name": "g", "regex": "(x)"}]}
FORMS = {"mode": "full_page", "document_types": [FORM, GENERIC]}


def test_a_later_page_of_a_form_is_typed_but_its_fields_are_not_read():
    page = "Course Details ... (see the application form for terms)"
    doc_type, reads = match_page(page, FORMS)
    assert doc_type is FORM and reads is False
    assert page_zone_groups(FORMS, page) == ([], "Form")


def test_the_title_page_of_the_form_is_typed_and_read():
    doc_type, reads = match_page("LOAN APPLICATION  Account number", FORMS)
    assert doc_type is FORM and reads is True
    assert names(page_zone_groups(FORMS, "loan application")[0]) == [(["account"], "zone")]


def test_an_earlier_type_claims_a_page_before_a_later_types_pattern_is_tried():
    """The generic type would match "application form" on the form's later
    page, but the specific form is listed first and claims it by its heading."""
    assert classify_page("course details application form", FORMS) is FORM


def test_pages_that_match_nothing_have_no_type_even_with_continuations():
    assert match_page("a letter", FORMS) == (None, False)


def test_a_broken_continuation_pattern_is_skipped_not_fatal():
    ocr = {"document_types": [{"name": "A", "regex": "x", "continuation_regex": "(",
                               "zones": []}, {"name": "B", "regex": "y", "zones": []}]}
    assert classify_page("y", ocr)["name"] == "B"
