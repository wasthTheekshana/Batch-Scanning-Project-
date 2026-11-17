"""The dashboard's script files, in the order the page loads them."""
import re
from pathlib import Path

DASHBOARD = Path(__file__).parents[2] / "central" / "dashboard"


def script_names() -> list[str]:
    page = (DASHBOARD / "index.html").read_text(encoding="utf-8")
    return re.findall(r'src="/dashboard/([\w.]+\.js)"', page)


def all_js() -> str:
    """The dashboard's own scripts (not form.js) as one text."""
    return "\n".join((DASHBOARD / n).read_text(encoding="utf-8") for n in script_names() if n != "form.js")
