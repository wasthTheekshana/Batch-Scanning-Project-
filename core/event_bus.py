"""Qt signal/slot event bus for inter-panel communication (HLD §16).

Pipeline worker threads emit via `publish_*` (thread-safe: Qt queues
cross-thread signals onto the UI event loop automatically).
"""
from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal


class EventBus(QObject):
    job_progress = pyqtSignal(dict)     # {stage, status, job_id/batch_id, ...}
    qc_ready = pyqtSignal(str)          # document_id entering review queue
    batch_complete = pyqtSignal(str)    # batch_id
    login_changed = pyqtSignal(object)  # User | None

    def publish_pipeline_event(self, event: dict) -> None:
        """Adapter for Pipeline.subscribe — fan out to typed signals."""
        self.job_progress.emit(event)
        if event.get("stage") == "delivery" and \
                event.get("status") == "held_for_review":
            self.qc_ready.emit(event.get("document_id", ""))
        if event.get("stage") == "batch" and event.get("status") == "complete":
            self.batch_complete.emit(event.get("batch_id", ""))
