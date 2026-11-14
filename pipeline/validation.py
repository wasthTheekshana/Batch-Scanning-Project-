"""Format checks for index values an operator types or the reader produced.

A check never blocks anything by itself: it returns a plain message so the
Index editor can mark the field and ask before saving a value that looks
wrong. An empty value is not an error (the field may simply be blank).
"""
from __future__ import annotations

import re
from datetime import date

KINDS = ("nic", "date", "phone", "email")
_NAME_HINTS = (("nic", "nic"), ("birth", "date"), ("dob", "date"), ("date", "date"),
               ("mobile", "phone"), ("phone", "phone"), ("telephone", "phone"),
               ("email", "email"), ("e_mail", "email"))


def kind_for(zone: dict) -> str | None:
    """The check for a field: the zone's own `validate`, else guessed from
    its name (nic_number, date_of_birth, joint_mobile, email...)."""
    chosen = zone.get("validate")
    if chosen:
        return chosen if chosen in KINDS else None
    name = str(zone.get("name", "")).lower()
    for hint, kind in _NAME_HINTS:
        if hint in name:
            return kind
    return None


def _nic(value: str) -> str | None:
    v = re.sub(r"\s", "", value)
    if re.fullmatch(r"\d{9}[VvXx]", v):
        days = int(v[2:5])
    elif re.fullmatch(r"\d{12}", v):
        days = int(v[4:7])
        if not 1900 <= int(v[:4]) <= date.today().year:
            return "The birth year in this NIC number looks wrong."
    else:
        return "A NIC number is 9 digits and V or X, or 12 digits."
    if not (1 <= days <= 366 or 501 <= days <= 866):
        return "The day-of-year part of this NIC number is not valid."
    return None


def _date(value: str) -> str | None:
    v = value.strip()
    parts = None
    if re.fullmatch(r"\d{8}", v):
        parts = (int(v[4:]), int(v[2:4]), int(v[:2]))              # ddmmyyyy
    else:
        m = re.fullmatch(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})", v)
        if m:
            parts = (int(m[3]), int(m[2]), int(m[1]))
        else:
            m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", v)
            if m:
                parts = (int(m[1]), int(m[2]), int(m[3]))
    if parts is None:
        return "Enter the date as DD/MM/YYYY."
    try:
        d = date(*parts)
    except ValueError:
        return "This is not a real calendar date."
    if d.year < 1900 or d > date.today():
        return "This date is out of range."
    return None


def _phone(value: str) -> str | None:
    digits = re.sub(r"[\s+\-()]", "", value)
    if not re.fullmatch(r"\d{9,12}", digits):
        return "A phone number has 9 to 12 digits."
    return None


def _email(value: str) -> str | None:
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value.strip()):
        return "This does not look like an email address."
    return None


_CHECKS = {"nic": _nic, "date": _date, "phone": _phone, "email": _email}


def validate(kind: str | None, value) -> str | None:
    """A message when `value` is wrong for `kind`, else None."""
    text = "" if value is None else str(value)
    if not kind or not text.strip() or kind not in _CHECKS:
        return None
    return _CHECKS[kind](text)
