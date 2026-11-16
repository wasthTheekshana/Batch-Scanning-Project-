"""Workflow designer (HLD §16, Phase 2 §18 weeks 5-6).

Edits the same project JSON the engine runs — the rule builder emits
the exact Phase 1 DSL, validated by RuleEngine before save, so there is
complete backwards compatibility (HLD §8). Saving goes through
ProfileManager.save which versions + archives automatically.
"""
from __future__ import annotations

from PyQt6.QtWidgets import QInputDialog, QMessageBox, QWidget  # noqa: F401  (tests patch these on this module)

from ui.designer_common import (BARCODE_ROLES, CENTRAL_UPDATED_NOTICE, EXPORT_FORMATS,  # noqa: F401  (re-exported)
                                INDEX_FORMATS, MANAGED_BANNER, QC_PRESETS, QC_STAGES, SAVE_LABEL,
                                SAVE_LOCAL_LABEL, STRATEGIES, UNREADABLE_BANNER, WRITE_FAILED_TEXT,
                                StoredFileUnreadable, validation_text)
from ui.designer_config import ConfigMixin
from ui.designer_form import FormBuilderMixin
from ui.designer_rules import RulesMixin
from ui.designer_save import SaveMixin
from ui.designer_selection import SelectionMixin
from ui.designer_zones import ZonesAndTypesMixin
from ui.rule_dialog import ACTIONS, OPERATORS, RuleDialog  # noqa: F401  (re-exported for existing callers)


class WorkflowDesigner(FormBuilderMixin, SelectionMixin, ConfigMixin, ZonesAndTypesMixin, SaveMixin, RulesMixin,
                       QWidget):
    def __init__(self, db, profile_manager, current_user, parent=None,
                 bus=None):
        super().__init__(parent)
        self.db = db
        self.pm = profile_manager
        self.user = current_user
        self.config: dict | None = None
        self.can_edit = current_user is not None and \
            current_user.can("project.edit")

        layout = self._build_header()
        self._build_feature_box(layout)
        self._build_separation_box(layout)
        self._build_qc_box(layout)
        self._build_export_box(layout)
        self._build_zones_box(layout)
        self._build_types_box(layout)
        self._build_sftp_box(layout)
        self._build_cd_box(layout)

        # on an unmanaged project the shorthand toggles still drive the
        # checkboxes (the user may then change a checkbox independently)
        self.feature_checks["sftp"].toggled.connect(
            self.sftp_enabled.setChecked)
        self.feature_checks["cd_burn"].toggled.connect(
            self.cd_enabled.setChecked)

        self._build_rules_box(layout)
        layout.addStretch(1)
        self._finish_form()

        self.refresh_projects()
        if bus is not None:
            bus.central_synced.connect(self.on_central_synced)
