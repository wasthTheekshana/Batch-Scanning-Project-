"""Keep one failed button click from taking the whole application down.

PyQt6 aborts the process (a Windows fail-fast in Qt6Core.dll) when a Python
error escapes a slot and nobody handles it. install() replaces that with:
the full traceback goes to a log file, the operator sees a plain message
naming the kind of error, and the application carries on.

The message never includes the error's own text, which can quote documents or
configuration (see the project's logging rule); the log file has the detail.
"""
from __future__ import annotations

import logging
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
                f.write("".join(traceback.format_exception(exc_type, exc, tb)))
                f.write("\n")
        except OSError:
            pass
        log.error("unhandled %s (see crash.log)", exc_type.__name__)
        if show is not None and not _guard["busy"]:
            _guard["busy"] = True      # one box at a time, never recursive
            try:
                show("DOK Scanner", describe(exc_type))
            except Exception:
                pass
            finally:
                _guard["busy"] = False

    sys.excepthook = handler
    threading.excepthook = lambda args: handler(
        args.exc_type, args.exc_value, args.exc_traceback)
    return handler
