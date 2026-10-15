"""OCR benchmark: how often is each index field read exactly right?

Fills the real bank form (docs/education-loan-english.pdf) with random but
valid customers, "scans" it under several conditions (resolution, shrunk and
crooked copy, black-and-white, noise), reads it exactly as the pipeline does
(Tesseract words -> anchor alignment -> zone reader), and reports per field and
per condition the exact-match rate and the character accuracy.

    python scripts/ocr_benchmark.py                 # 8 customers, all conditions
    python scripts/ocr_benchmark.py -n 20 --seed 3  # bigger, other random set
    python scripts/ocr_benchmark.py --real DIR      # real scans, see below
    python scripts/ocr_benchmark.py --report docs/OCR_BENCHMARK.md

IMPORTANT: the synthetic forms hold TYPED text. They measure the reader and
the alignment, not handwriting, so they are an upper bound for what a pen-filled
form will give. `--real DIR` measures real scans: put each scan in DIR as
name.(png|jpg|tif|pdf) beside name.json holding the true values
({"nic_number": "199012345678", ...}); only the fields listed there count.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import cv2
import fitz
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import loan_form  # noqa: E402

from ocr.tesseract_engine import ocr_page  # noqa: E402
from ocr.zone_extractor import extract_fields  # noqa: E402

FORM = ROOT / "docs" / "education-loan-english.pdf"
FIRST = ["NIMAL", "KAMALA", "SUNIL", "ANURA", "DILANI", "RUWAN", "SAMANTHI",
         "CHAMARA", "NADEESHA", "PRASAD", "IRESHA", "LAKMAL"]
LAST = ["PERERA", "SILVA", "FERNANDO", "JAYASINGHE", "WIJEWARDENA", "DE ALWIS",
        "RATHNAYAKE", "GUNASEKARA", "HERATH", "KUMARA"]
BRANCHES = ["Colombo Fort", "Kandy", "Galle", "Negombo", "Jaffna", "Matara"]
STREETS = ["Temple Road", "Lake Drive", "Station Road", "Main Street", "Hill Street"]


def customer(rng: random.Random) -> dict:
    """One random applicant whose values are well-formed."""
    born = date(1960, 1, 1) + timedelta(days=rng.randrange(0, 365 * 40))
    if rng.random() < 0.5:                                  # new 12-digit NIC
        nic = f"{born.year}{born.timetuple().tm_yday:03d}{rng.randrange(10000, 99999)}"
    else:                                                   # old 9 digits + V
        nic = f"{born.year % 100:02d}{born.timetuple().tm_yday:03d}{rng.randrange(1000, 9999)}V"
    name = f"{rng.choice(FIRST)} {rng.choice(FIRST)} {rng.choice(LAST)}"
    return {
        "account_number": "".join(rng.choice("0123456789") for _ in range(10)),
        "branch": rng.choice(BRANCHES),
        "applicant_name": name,
        "date_of_birth": born.strftime("%d%m%Y"),
        "mobile": "07" + "".join(rng.choice("0123456789") for _ in range(8)),
        "email": name.split()[0].lower() + "." + name.split()[-1].lower().replace(" ", "") + "@example.com",
        "nic_number": nic,
        "passport_number": "N" + "".join(rng.choice("0123456789") for _ in range(7)),
        "address": f"{rng.randrange(1, 200)} {rng.choice(STREETS)}, Colombo {rng.randrange(1, 15):02d}",
    }


# -- conditions: how the "scan" differs from the clean form ---------------------------
def render(pdf: Path, dpi: int) -> np.ndarray:
    with fitz.open(str(pdf)) as doc:
        pix = doc[0].get_pixmap(dpi=dpi, colorspace=fitz.csGRAY)
        return np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width).copy()


def _warp(page: np.ndarray, scale: float, angle: float, shift) -> np.ndarray:
    h, w = page.shape
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, scale)
    m[:, 2] += shift
    return cv2.warpAffine(page, m, (w, h), borderValue=255)


def _noisy(page: np.ndarray, rng: random.Random) -> np.ndarray:
    gen = np.random.default_rng(rng.randrange(1 << 30))
    out = cv2.GaussianBlur(page, (3, 3), 0).astype(np.int16)
    out += gen.normal(0, 14, page.shape).astype(np.int16)
    speck = gen.random(page.shape)
    out[speck < 0.0015] = 0
    return np.clip(out, 0, 255).astype(np.uint8)


CONDITIONS = {
    "clean 300 dpi": lambda pdf, rng: render(pdf, 300),
    "200 dpi": lambda pdf, rng: render(pdf, 200),
    "shrunk, shifted, crooked": lambda pdf, rng: _warp(render(pdf, 300), 0.94, 1.0, (50, 40)),
    "black and white": lambda pdf, rng: cv2.threshold(render(pdf, 300), 150, 255, cv2.THRESH_BINARY)[1],
    "noisy scan": lambda pdf, rng: _noisy(render(pdf, 300), rng),
}


# -- reading and scoring ---------------------------------------------------------------
def read_page(page: np.ndarray) -> dict[str, str]:
    """The pipeline's own path: OCR words, anchor alignment, zone reader."""
    words = ocr_page(page).words
    zones = [{**z, "anchors": loan_form.anchors()} for z in loan_form.zones()]
    return {k: v.value for k, v in extract_fields(page, zones, words=words).items()}


def edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def char_accuracy(truth: str, got: str) -> float:
    if not truth and not got:
        return 1.0
    return max(0.0, 1 - edit_distance(truth, got) / max(len(truth), len(got)))


class Tally:
    """Exact matches and character accuracy per (group, field)."""

    def __init__(self):
        self.rows: dict[tuple[str, str], list[tuple[bool, float]]] = {}

    def add(self, group: str, field: str, truth: str, got: str) -> None:
        self.rows.setdefault((group, field), []).append(
            (got.strip() == truth.strip(), char_accuracy(truth.strip(), got.strip())))

    def groups(self) -> list[str]:
        return list(dict.fromkeys(g for g, _ in self.rows))

    def fields(self) -> list[str]:
        return list(dict.fromkeys(f for _, f in self.rows))

    def exact(self, group=None, field=None) -> float | None:
        cells = [c for (g, f), v in self.rows.items()
                 if group in (None, g) and field in (None, f) for c in v]
        return sum(e for e, _ in cells) / len(cells) if cells else None

    def chars(self, group=None, field=None) -> float | None:
        cells = [c for (g, f), v in self.rows.items()
                 if group in (None, g) and field in (None, f) for c in v]
        return sum(a for _, a in cells) / len(cells) if cells else None


def run_synthetic(n: int, seed: int, conditions=None, progress=print) -> Tally:
    rng = random.Random(seed)
    tally = Tally()
    work = ROOT / "data" / "_benchmark_tmp"
    work.mkdir(parents=True, exist_ok=True)
    try:
        for i in range(n):
            values = customer(rng)
            pdf = loan_form.fill_first_page(FORM, work / f"c{i}.pdf", values)
            for name, make in (conditions or CONDITIONS).items():
                read = read_page(make(pdf, rng))
                for field, truth in values.items():
                    tally.add(name, field, truth, read.get(field, ""))
            progress(f"  customer {i + 1}/{n} done")
    finally:
        for f in work.glob("*.pdf"):
            f.unlink(missing_ok=True)
        try:
            work.rmdir()
        except OSError:
            pass
    return tally


def run_real(folder: Path, progress=print) -> Tally:
    tally = Tally()
    for truth_file in sorted(folder.glob("*.json")):
        truth = json.loads(truth_file.read_text(encoding="utf-8"))
        scan = next((p for ext in (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".pdf")
                     if (p := truth_file.with_suffix(ext)).exists()), None)
        if scan is None:
            progress(f"  {truth_file.name}: no scan beside it, skipped")
            continue
        if scan.suffix == ".pdf":
            page = render(scan, 300)
        else:
            page = cv2.imread(str(scan), cv2.IMREAD_GRAYSCALE)
        read = read_page(page)
        for field, value in truth.items():
            tally.add("real scans", field, str(value), read.get(field, ""))
        progress(f"  {scan.name} read")
    return tally


# -- report --------------------------------------------------------------------------------
def pct(x) -> str:
    return "   - " if x is None else f"{100 * x:5.1f}%"


def report(tally: Tally, title: str, note: str = "") -> str:
    groups, fields = tally.groups(), tally.fields()
    lines = [f"# {title}", ""]
    if note:
        lines += [note, ""]
    lines += ["Exact-match rate (the value is read exactly right):", "",
              "| Field | " + " | ".join(groups) + " | All |",
              "|---|" + "---|" * (len(groups) + 1)]
    for f in fields:
        lines.append(f"| {f} | " + " | ".join(pct(tally.exact(g, f)) for g in groups)
                     + f" | {pct(tally.exact(None, f))} |")
    lines.append("| **All fields** | " + " | ".join(pct(tally.exact(g)) for g in groups)
                 + f" | **{pct(tally.exact())}** |")
    lines += ["", "Character accuracy (1 - edit distance / length):", "",
              "| Field | " + " | ".join(groups) + " | All |",
              "|---|" + "---|" * (len(groups) + 1)]
    for f in fields:
        lines.append(f"| {f} | " + " | ".join(pct(tally.chars(g, f)) for g in groups)
                     + f" | {pct(tally.chars(None, f))} |")
    lines.append("| **All fields** | " + " | ".join(pct(tally.chars(g)) for g in groups)
                 + f" | **{pct(tally.chars())}** |")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("-n", type=int, default=8, help="synthetic customers (default 8)")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--real", type=Path, help="folder of real scans with name.json truth files")
    ap.add_argument("--report", type=Path, help="also write the report to this .md file")
    args = ap.parse_args(argv)
    started = time.time()
    if args.real:
        tally = run_real(args.real)
        title = f"OCR benchmark on real scans ({args.real.name})"
        note = "Fields counted: those listed in each scan's .json."
    else:
        if not FORM.exists():
            print(f"The blank form {FORM} is missing.")
            return 2
        tally = run_synthetic(args.n, args.seed)
        title = "OCR benchmark on the education loan form (synthetic)"
        note = (f"{args.n} random customers x {len(CONDITIONS)} scan conditions, seed "
                f"{args.seed}. The forms hold TYPED text: this measures the reader and "
                "the alignment, not handwriting, so it is an upper bound for pen-filled forms.")
    text = report(tally, title, note) + f"\n(took {time.time() - started:.0f} s)\n"
    print(text)
    if args.report:
        args.report.write_text(text, encoding="utf-8")
        print(f"report written to {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
