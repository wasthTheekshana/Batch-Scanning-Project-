"""Blank-page document boundary logic (HLD §4 Layer 4).

A blank page ends the current document and is removed after the split
(it is a physical separator sheet, not document content).
"""
from __future__ import annotations

from typing import Sequence, TypeVar

P = TypeVar("P")


def split_on_blanks(pages: Sequence[P], is_blank_flags: Sequence[bool]) -> list[list[P]]:
    """Split a page sequence into documents at blank separator pages.

    `is_blank_flags[i]` says whether `pages[i]` is blank. Blank pages are
    dropped; consecutive blanks and leading/trailing blanks produce no
    empty documents.
    """
    docs: list[list[P]] = []
    current: list[P] = []
    for page, blank in zip(pages, is_blank_flags):
        if blank:
            if current:
                docs.append(current)
                current = []
        else:
            current.append(page)
    if current:
        docs.append(current)
    return docs
