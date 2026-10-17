"""docs/CENTRAL_API.md is generated from the service's routes: it must be current."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import gen_api_doc  # noqa: E402


def test_the_api_document_matches_the_service():
    assert gen_api_doc.main(["--check"]) == 0, "run: python scripts/gen_api_doc.py"


def test_every_admin_route_requires_a_session_except_login():
    text = gen_api_doc.render()
    admin_rows = [ln for ln in text.splitlines() if ln.startswith("| ") and "`/admin/" in ln]
    open_rows = [ln for ln in admin_rows if "nobody yet" in ln]
    assert admin_rows and [ln for ln in open_rows if "/admin/login" not in ln] == []


def test_pc_routes_use_the_api_key_and_registration_the_one_time_code():
    text = gen_api_doc.render()
    assert "| GET | `/pcs/{pc_id}/config` | PC API key (Bearer) |" in text
    assert "| POST | `/pcs/register` | nobody yet: the one-time enrolment code" in text
