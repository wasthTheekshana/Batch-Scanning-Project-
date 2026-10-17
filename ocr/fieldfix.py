"""Correcting what the handwriting reader returns, using what is known about each field.

A reader is never certain, but a mobile number has ten digits, an e-mail address ends in
a real mail domain, and a branch is one of the bank's branches. Each fixer takes the
reader's text and returns (value, confidence factor): the factor lowers the confidence
when the text had to be repaired a lot, so the operator is still asked to check it.
"""
from __future__ import annotations

import difflib
import os
import re
import sys
from functools import lru_cache
from pathlib import Path

DOMAINS = ["gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "icloud.com",
           "live.com", "sltnet.lk", "slt.lk", "mail.com", "protonmail.com",
           "combank.lk", "dialog.lk", "mobitel.lk"]


# ---- names -------------------------------------------------------------------------------------
def fix_name(text: str, snap: bool = True) -> tuple[str, float]:
    """Letters, spaces, dots and apostrophes only; stray box-edge marks dropped; each word
    capitalised."""
    cleaned = re.sub(r"([A-Z])'([A-Z][a-z]+)", lambda m: m.group(1) + chr(1) + m.group(2), text or "")   # O'Neil, D'Silva keep their apostrophe
    cleaned = re.sub(r"[^A-Za-z. " + chr(1) + "]", " ", cleaned.replace("-", "")).replace(chr(1), "'")
    words = cleaned.split()
    # a vertical box edge is read as a lone "I" or "l" at either end
    while len(words) > 1 and words[0] in ("I", "l", "-", ".", "i"):
        words.pop(0)
    while len(words) > 1 and words[-1] in ("I", "l", "-", ".", "i"):
        words.pop()
    words = [w.strip(".-'") if len(w.strip(".-'")) > 1 or w.upper() in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
             else w for w in words]
    words = ["'".join(part.capitalize() for part in w.split("'")) if len(w) > 2 else w.upper()
             for w in words if w]
    snapped, factor = [], 1.0
    known = set(w.lower() for w in name_words()) if snap else set()
    for w in words:
        if snap and len(w) >= 5 and w.lower() not in known and "." not in w:
            close = difflib.get_close_matches(w.lower(), [k for k in known if abs(len(k) - len(w)) <= 2],
                                              n=1, cutoff=0.8)
            if close:
                w, factor = close[0].capitalize(), min(factor, 0.9)
        snapped.append(w)
    return " ".join(snapped), factor


def name_variants(text: str, limit: int = 40) -> list[str]:
    """The cleaned name and the names it may have meant: every word of it, kept as read or
    replaced by a close name from the list. The handwriting model then picks the one that
    fits the pen strokes best."""
    import itertools
    base, _ = fix_name(text, snap=False)
    known = [k for k in name_words()]
    options = []
    for w in base.split():
        alts = [w]
        if len(w) >= 4 and "." not in w:
            alts += [c.capitalize() for c in difflib.get_close_matches(
                w.lower(), [k.lower() for k in known if abs(len(k) - len(w)) <= 2], n=2, cutoff=0.74)
                if c.capitalize() != w]
        options.append(alts)
    combos = [" ".join(c) for c in itertools.islice(itertools.product(*options), limit)] if options else []
    return list(dict.fromkeys(combos))


def name_prior(name: str) -> float:
    """Bonus added to the model's score for a candidate name made of known names."""
    known = {w.lower() for w in name_words()}
    words = [w for w in name.split() if len(w) > 2]
    return 1.6 * sum(w.lower() in known for w in words) / len(words) if words else 0.0


def names_file() -> Path:
    own = os.environ.get("DOK_NAMES")
    if own and Path(own).exists():
        return Path(own)
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1])) / "ocr" / "names.txt"


@lru_cache(maxsize=1)
def name_words() -> list[str]:
    path = names_file()
    if not path.exists():
        return []
    return [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.startswith("#")]


# ---- mobile ---------------------------------------------------------------------------------------
_LOOK = str.maketrans({"O": "0", "o": "0", "D": "0", "l": "1", "I": "1", "|": "1", "i": "1",
                       "S": "5", "s": "5", "B": "8", "Z": "2", "z": "2", "g": "9", "q": "9"})


def fix_mobile(text: str) -> tuple[str, float]:
    digits = re.sub(r"\D", "", (text or "").translate(_LOOK))
    if len(digits) == 9 and digits[0] == "7":          # the leading 0 was dropped
        digits = "0" + digits
    if digits.startswith("94") and len(digits) == 11:  # +94 format
        digits = "0" + digits[2:]
    return digits, 1.0 if len(digits) == 10 and digits.startswith("0") else 0.6


# ---- NIC ---------------------------------------------------------------------------------------------
def fix_nic(text: str) -> tuple[str, float]:
    t = re.sub(r"[^0-9VvXx]", "", (text or "").translate(_LOOK)).upper()
    if re.fullmatch(r"\d{9}[VX]", t) or re.fullmatch(r"\d{12}", t):
        return t, 1.0
    m = re.match(r"(\d{9})[VX]?", t)
    if len(t) in (9, 10) and m:                        # old number missing its letter
        return t if len(t) == 10 else t + "V", 0.7
    return t, 0.5


# ---- e-mail -------------------------------------------------------------------------------------------
def _domain_split(t: str) -> tuple[str, str] | None:
    """(local part, domain) found by matching the end of the text to the known mail domains."""
    t = t.replace("-", ".")
    best = (0.0, None, None)
    for domain in DOMAINS:
        n = len(domain)
        for width in range(max(4, n - 2), n + 3):
            if width > len(t) - 1:
                continue
            tail = t[-width:]
            score = difflib.SequenceMatcher(None, tail, domain).ratio()
            if score > best[0] + 1e-9 or (abs(score - best[0]) < 1e-9 and domain == "gmail.com"):
                best = (score, t[:-width], domain)
    return (best[1], best[2]) if best[0] >= 0.62 and best[1] else None


def email_candidates(*texts: str) -> list[str]:
    """Candidates from several readings of the same writing."""
    out: list[str] = []
    for text in texts:
        out += _email_candidates(text)
    return list(dict.fromkeys(out))


def _email_candidates(text: str) -> list[str]:
    """Addresses the reading may have meant: the domain snapped to a real provider, and the
    local part with 0-3 trailing characters dropped (the @ is often read as letters)."""
    t = (text or "").strip().lower().replace(" ", "").replace(",", ".")
    t = re.sub(r"[^a-z0-9@._\-+]", "", t).replace("-", ".") if "@" not in t else         re.sub(r"[^a-z0-9@._\-+]", "", t)
    t = re.sub(r"\.{2,}", ".", t).strip(".-_")
    if "@" in t:
        local, _, domain = t.partition("@")
        domains = difflib.get_close_matches(domain, DOMAINS, n=3, cutoff=0.6) or [domain]
        if "mail" in domain and "gmail.com" not in domains:
            domains.append("gmail.com")                  # "mail.com" is usually a gmail.com that lost its g
        locals_ = [local]
    else:
        split = _domain_split(t)
        if not split:
            return []
        local, domain = split
        domains, locals_ = [domain], [local.strip(".-_")]
    if "gmail.com" not in domains:
        domains.append("gmail.com")
    out = []
    for loc in locals_:
        for cut in range(0, 4):
            base = loc[:len(loc) - cut] if cut else loc
            if len(base) >= 2 and re.fullmatch(r"[a-z0-9._+\-]+", base):
                out += [f"{base}@{d}" for d in domains]
    return list(dict.fromkeys(out))


# how likely each provider is on a bank form, as a bonus on the model's own score
DOMAIN_PRIOR = {"gmail.com": 0.7, "yahoo.com": 0.35, "hotmail.com": 0.3, "outlook.com": 0.2,
                "sltnet.lk": 0.2, "icloud.com": 0.1}


def email_prior(address: str) -> float:
    """Plausibility bonus/penalty for a candidate address (added to the model's score)."""
    local, _, domain = address.partition("@")
    bonus = DOMAIN_PRIOR.get(domain, 0.0)
    bonus -= 0.35 * len(re.findall(r"[_]", local))              # underscores are rare in new addresses
    bonus -= 0.3 * len(re.findall(r"[._-]{2,}|[._-][a-z][._-]", local))
    return bonus


def fix_email(text: str) -> tuple[str, float]:
    """x@domain.tld, repaired: spaces and stray marks out, the @ restored, and the domain
    snapped to a real mail provider when it is nearly one."""
    t = (text or "").strip().lower()
    t = t.replace(" ", "").replace(",", ".").replace("©", "@").replace("(a)", "@")
    t = re.sub(r"[^a-z0-9@._\-+]", "", t)
    t = re.sub(r"\.{2,}", ".", t).strip(".-_")
    if t.count("@") == 1:
        local, domain = t.split("@")
        snapped = difflib.get_close_matches(domain, DOMAINS, n=1, cutoff=0.75)
        domain = snapped[0] if snapped else domain
        ok = bool(local) and re.fullmatch(r"[a-z0-9.\-]+\.[a-z]{2,}", domain)
        return f"{local}@{domain}", 1.0 if ok else 0.6
    split = _domain_split(t.replace("@", ""))
    if split:
        local = split[0].strip(".-_")
        return f"{local}@{split[1]}", 0.65
    return t, 0.3


# ---- branch ----------------------------------------------------------------------------------------------
def branch_file() -> Path:
    """The bank's own list (branches.txt, one per line) in the data folder wins over the
    built-in list."""
    own = os.environ.get("DOK_BRANCHES")
    if own and Path(own).exists():
        return Path(own)
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1])) / "ocr" / "branches.txt"


@lru_cache(maxsize=1)
def branches() -> list[str]:
    path = branch_file()
    if not path.exists():
        return []
    return [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.startswith("#")]


def fix_branch(text: str, ranked: list[tuple[str, float]] | None = None) -> tuple[str, float]:
    """The branch name: the model's own ranking of the bank's branches when given, else the
    nearest branch to the text. Anything not close to a branch is returned as read."""
    names = branches()
    cleaned = re.sub(r"[^A-Za-z ]", " ", text or "").strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    if ranked:
        (best, score), rest = ranked[0], ranked[1:]
        margin = score - rest[0][1] if rest else 1.0
        confidence = min(1.0, max(0.2, (2.718 ** score) * 1.15 + min(margin, 1.0) * 0.5))
        return best, confidence
    if names and cleaned:
        close = difflib.get_close_matches(cleaned.title(), names, n=1, cutoff=0.6)
        if close:
            return close[0], 0.8
    return cleaned.title(), 0.4
