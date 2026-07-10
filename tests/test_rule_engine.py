import pytest

from separation.rule_engine import RuleEngine, RuleSetError


def rs(rules):
    return {"rule_set_id": "rs1", "version": 1, "rules": rules}


def rule(condition, actions, rid="r1"):
    return {"id": rid, "name": rid, "condition": condition, "actions": actions}


def run(condition, document):
    """Evaluate a single rule; return True if its actions were applied."""
    engine = RuleEngine(rs([rule(condition, [{"type": "add_flag", "value": "hit"}])]))
    return bool(engine.evaluate(document))


# ---- operators --------------------------------------------------------------

def test_eq_neq():
    assert run({"field": "document_type", "op": "eq", "value": "invoice"},
               {"document_type": "invoice"})
    assert not run({"field": "document_type", "op": "eq", "value": "invoice"},
                   {"document_type": "po"})
    assert run({"field": "document_type", "op": "neq", "value": "invoice"},
               {"document_type": "po"})


def test_contains():
    doc = {"ocr_text": "TOTAL DUE: 500"}
    assert run({"field": "ocr_text", "op": "contains", "value": "TOTAL"}, doc)
    assert run({"field": "ocr_text", "op": "not_contains", "value": "VOID"}, doc)


def test_is_empty_and_missing_field():
    assert run({"field": "extracted_fields.invoice_no", "op": "is_empty"},
               {"extracted_fields": {}})
    assert run({"field": "nonexistent.path", "op": "is_empty"}, {})
    assert not run({"field": "nonexistent.path", "op": "eq", "value": "x"}, {})
    assert run({"field": "extracted_fields.invoice_no", "op": "is_not_empty"},
               {"extracted_fields": {"invoice_no": "INV-1"}})


def test_numeric_comparisons():
    doc = {"qc_score": 75}
    assert run({"field": "qc_score", "op": "gt", "value": 60}, doc)
    assert run({"field": "qc_score", "op": "lte", "value": 75}, doc)
    assert not run({"field": "qc_score", "op": "gte", "value": 80}, doc)
    # non-numeric value never matches numeric ops
    assert not run({"field": "qc_score", "op": "gt", "value": 60}, {"qc_score": "n/a"})


def test_matches_regex():
    assert run({"field": "extracted_fields.invoice_no", "op": "matches_regex",
                "value": r"^INV-\d{4}$"},
               {"extracted_fields": {"invoice_no": "INV-1234"}})


def test_nested_and_or_not():
    cond = {"operator": "AND", "checks": [
        {"field": "document_type", "op": "eq", "value": "invoice"},
        {"operator": "OR", "checks": [
            {"field": "qc_score", "op": "lt", "value": 60},
            {"field": "flags", "op": "contains", "value": "blur"},
        ]},
        {"operator": "NOT", "checks": [
            {"field": "validation_status", "op": "eq", "value": "rejected"},
        ]},
    ]}
    assert run(cond, {"document_type": "invoice", "qc_score": 40,
                      "validation_status": "pending_qc"})
    assert not run(cond, {"document_type": "invoice", "qc_score": 90,
                          "flags": [], "validation_status": "pending_qc"})


# ---- actions ----------------------------------------------------------------

def test_all_actions_mutate_document():
    doc = {"document_type": "invoice", "extracted_fields": {}}
    engine = RuleEngine(rs([rule(
        {"field": "document_type", "op": "eq", "value": "invoice"},
        [
            {"type": "set_folder", "value": "Exceptions/Missing"},
            {"type": "set_filename", "value": "{date}_{counter:04d}"},
            {"type": "set_validation_status", "value": "pending_qc"},
            {"type": "add_flag", "value": "missing_invoice_number"},
            {"type": "set_metadata_field",
             "value": {"field": "doc_class", "value": "A"}},
            {"type": "trigger_delivery", "value": "local_folder"},
            {"type": "skip_stage", "value": "punch_hole"},
        ])]))
    applied = engine.evaluate(doc)
    assert len(applied) == 7
    assert doc["output_folder"] == "Exceptions/Missing"
    assert doc["filename_template"] == "{date}_{counter:04d}"
    assert doc["validation_status"] == "pending_qc"
    assert doc["flags"] == ["missing_invoice_number"]
    assert doc["extracted_fields"]["doc_class"] == "A"
    assert doc["trigger_channels"] == ["local_folder"]
    assert doc["skip_stages"] == ["punch_hole"]


def test_hld_example_rule():
    """The exact example from HLD §8.1."""
    rule_set = {
        "rule_set_id": "uuid", "version": 2, "parent_version": 1,
        "rules": [{
            "id": "r001", "name": "Flag missing invoice number",
            "condition": {
                "operator": "AND",
                "checks": [
                    {"field": "extracted_fields.invoice_no", "op": "is_empty"},
                    {"field": "document_type", "op": "eq", "value": "invoice"},
                ],
            },
            "actions": [
                {"type": "set_validation_status", "value": "pending_qc"},
                {"type": "add_flag", "value": "missing_invoice_number"},
                {"type": "set_folder", "value": "Exceptions/MissingInvoiceNo"},
            ],
        }],
    }
    doc = {"document_type": "invoice", "extracted_fields": {}}
    applied = RuleEngine(rule_set).evaluate(doc)
    assert {a["type"] for a in applied} == {
        "set_validation_status", "add_flag", "set_folder"}
    assert doc["output_folder"] == "Exceptions/MissingInvoiceNo"


def test_invalid_rule_set_rejected_at_load():
    with pytest.raises(RuleSetError):
        RuleEngine(rs([rule({"field": "x", "op": "bogus_op"},
                            [{"type": "add_flag", "value": "f"}])]))
    with pytest.raises(RuleSetError):
        RuleEngine({"rules": []})  # missing rule_set_id/version
