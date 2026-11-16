"""Constants and small helpers shared by the workflow designer's modules."""
from __future__ import annotations

from projects.profile_manager import ProjectConfigError
from projects.project_store import config_error_path


WRITE_FAILED_TEXT = ("The project file could not be written. Close other "
                     "programs using it and try again.")


QC_STAGES = ["punch_hole", "rotation", "margin", "color", "advanced"]


STRATEGIES = ["barcode", "blank_page", "zone", "fixed_pages", "document_type"]


BARCODE_ROLES = [
    ("the first page of a document (barcode sticker on the page)",
     "first_page"),
    ("a separate cover sheet (not part of the document)", "cover_sheet"),
    ("automatic: a cover sheet if it has hardly any other text", "auto"),
]


MANAGED_BANNER = ("Managed centrally: only scanner / export / delivery "
                  "can be changed on this PC")


CENTRAL_UPDATED_NOTICE = ("Updated by the central server - re-select this "
                          "project to load the new settings.")


UNREADABLE_BANNER = ("This project's file could not be read - it cannot be "
                     "edited here until it is repaired")


SAVE_LABEL = "Save (new version)"


SAVE_LOCAL_LABEL = "Save local settings"


# PDF/A is not supported; a project saved with the old "pdf_a" value is
# shown (and saved) as searchable_pdf, which is what it always produced.
EXPORT_FORMATS = ["searchable_pdf", "image_pdf", "tiff"]


INDEX_FORMATS = [("CSV (opens in Excel)", "csv"), ("XML", "xml"),
                 ("JSON", "json")]


# QC strictness presets (Enhancement Plan §4.3) - one-click threshold sets
# for document sensitivity (e.g. legal/financial vs routine correspondence).
# "Standard" matches DEFAULT_PROJECT's own qc defaults exactly.
QC_PRESETS = {
    "Strict": {"pass_score": 90, "blur_warn_threshold": 100,
              "blur_fail_threshold": 50, "dpi_warn": 250, "dpi_fail": 200},
    "Standard": {"pass_score": 80, "blur_warn_threshold": 80,
                "blur_fail_threshold": 30, "dpi_warn": 200, "dpi_fail": 150},
    "Lenient": {"pass_score": 65, "blur_warn_threshold": 60,
               "blur_fail_threshold": 15, "dpi_warn": 150, "dpi_fail": 100},
}


class StoredFileUnreadable(Exception):
    """Loading the stored project file failed (no content in this error)."""


def validation_text(exc: ProjectConfigError) -> str:
    """Fixed text plus at most the field path - the schema message quotes
    the offending value, so it is never shown."""
    path = config_error_path(exc)
    where = f" at {path}" if path else ""
    return (f"The configuration is not valid{where}. Correct that field and "
            "save again." if path else "The configuration is not valid.")
