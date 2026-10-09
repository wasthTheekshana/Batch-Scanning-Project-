"""SafeStr validator: rejects lone surrogates to prevent 500 errors at database bind."""
from typing import Annotated

from pydantic import AfterValidator


def _reject_lone_surrogates(value: str) -> str:
    """Validate that a string can be encoded to UTF-8 (rejects lone surrogates).

    Lone surrogates like \ud800 cannot be encoded to UTF-8 and would cause
    a 500 error at database bind. We catch this at validation time and return
    a 422 instead.
    """
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("invalid characters")
    return value


SafeStr = Annotated[str, AfterValidator(_reject_lone_surrogates)]
