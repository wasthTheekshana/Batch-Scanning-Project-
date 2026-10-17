"""How well are handwritten digits in form boxes read? Reads every boxed number of the
sample forms (the digits written on them are known) and counts right digits.

    python scripts/digit_benchmark.py SAMPLE_DIR
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import cv2

import bank_forms as bf
from ocr.comb import read_comb

# (page, zone name, charset, what is written)
CASES = [
    ("personal_1", "account_number", "digits", "3258341563"),
    ("personal_1", "nic_number", "nic", "553216789V"),
    ("personal_1", "date_of_birth", "digits", "23031990"),
    ("credit_1", "nic_number", "nic", "534210056718"),
    ("credit_1", "date_of_birth", "digits", "30051997"),
    ("credit_1", "mobile", "digits", "0760070011"),
]


def main(argv) -> int:
    samples = Path(argv[1])
    zones = {"personal_1": {z["name"]: z for z in bf.personal_zones()},
             "credit_1": {z["name"]: z for z in bf.credit_zones()}}
    right = total = exact = 0
    for page, name, charset, truth in CASES:
        img = cv2.imread(str(samples / f"{page}.png"))
        z = zones[page][name]
        got = read_comb(img[z["y"]:z["y"] + z["h"], z["x"]:z["x"] + z["w"]], charset).text
        same = sum(a == b for a, b in zip(got, truth)) if len(got) == len(truth) else 0
        right += same
        total += len(truth)
        exact += got == truth
        print(f"{page:11} {name:15} read {got:14} want {truth:14} {same}/{len(truth)}")
    print(f"\nDIGITS RIGHT {right}/{total}   FIELDS EXACT {exact}/{len(CASES)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
