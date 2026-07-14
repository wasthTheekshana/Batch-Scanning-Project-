from qc.base import QCStageResult
from qc.scorer import score_page


def r(stage, deduction=0, auto_fail=False, flags=None):
    return QCStageResult(stage=stage, deduction=deduction,
                         auto_fail=auto_fail, flags=flags or [])


def test_perfect_page_passes():
    v = score_page([r("punch_hole"), r("rotation"), r("margin"),
                    r("color"), r("advanced")])
    assert v.score == 100 and v.grade == "PASS"


def test_warn_band():
    v = score_page([r("advanced", 20), r("color", 10)])  # 70
    assert v.score == 70 and v.grade == "WARN"


def test_pass_boundary_at_80():
    assert score_page([r("advanced", 20)]).grade == "PASS"      # exactly 80
    assert score_page([r("advanced", 21)]).grade == "WARN"      # 79


def test_fail_boundary_at_59():
    assert score_page([r("a", 40)]).grade == "WARN"             # 60
    assert score_page([r("a", 41)]).grade == "FAIL"             # 59


def test_score_clamped_at_zero():
    v = score_page([r("a", 40), r("b", 40), r("c", 40)])
    assert v.score == 0 and v.grade == "FAIL"


def test_auto_fail_forces_fail_grade():
    # arithmetic says 70 (WARN) but auto_fail forces FAIL
    v = score_page([r("advanced", 30, auto_fail=True)])
    assert v.grade == "FAIL"


def test_custom_pass_score():
    v = score_page([r("a", 10)], pass_score=95)   # 90 < 95
    assert v.grade == "WARN"


def test_flags_and_detail_aggregated():
    v = score_page([r("advanced", 20, flags=["blur_warn"]),
                    r("color", 10, flags=["low_contrast"])])
    assert set(v.flags) == {"blur_warn", "low_contrast"}
    assert v.detail["advanced"]["deduction"] == 20
