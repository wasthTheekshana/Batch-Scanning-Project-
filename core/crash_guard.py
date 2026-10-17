"""Keep one failed button click from taking the whole application down.

PyQt6 aborts the process (a Windows fail-fast in Qt6Core.dll) when a Python
error escapes a slot and nobody handles it. install() replaces that with:
where it happened goes to a log file, the operator sees a plain message
naming the kind of error, and the application carries on.

Neither the message nor, by default, the log file includes the error's own
text: it can quote documents or configuration (a JSON error quotes the
document, a schema error quotes the offending value), and the project never
logs those. crash.log records the error type and where it happened (file,
line, function), which is what is needed to find the fault. A developer
reproducing a problem can set DOKSCANNER_CRASH_DETAIL=1 to also record the
full traceback text, on their own machine.
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

_guard: dict = {"busy": False}


def describe(exc_type: type) -> str:
    """The operator-facing text: the kind of error, nothing from its message."""
    return (f"Something went wrong ({exc_type.__name__}). The action was "
            "not completed and nothing was lost. Details were written to "
            "the log file (crash.log in the logs folder); please send it to "
            "support if this keeps happening.")


def _entry(exc_type, exc, tb) -> str:
    """The crash.log record: type and code locations only, unless the
    developer opt-in is set."""
    if os.environ.get("DOKSCANNER_CRASH_DETAIL") == "1":
        return "".join(traceback.format_exception(exc_type, exc, tb))
    frames = traceback.extract_tb(tb)
    where = "".join(f"  {f.filename}:{f.lineno} in {f.name}" + chr(10)
                    for f in frames)
    return f"{exc_type.__name__} (message withheld)" + chr(10) + where


def install(log_dir: str | Path,
            show: Callable[[str, str], None] | None = None) -> Callable:
    """Install the handler; returns it (tests call it directly)."""
    log_path = Path(log_dir) / "crash.log"

    def handler(exc_type, exc, tb) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"--- {datetime.now().isoformat(timespec='seconds')} "
                        f"(thread {threading.current_thread().name})\n")
                f.write(_entry(exc_type, exc, tb))
                f.write(chr(10))
        except OSError:
            pass
        log.error("unhandled %s (see crash.log)", exc_type.__name__)
        if show is not None and not _guard["busy"]:
            _guard["busy"] = True      # one box at a time, never recursive
            try:
                show("DOK Scanner", describe(exc_type))
            except Exception:
                log.debug("could not show the crash message", exc_info=True)
            finally:
                _guard["busy"] = False

    sys.excepthook = handler
    threading.excepthook = lambda args: handler(
        args.exc_type, args.exc_value, args.exc_traceback)
    return handler
