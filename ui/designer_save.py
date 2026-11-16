"""Saving a project (a new version), cloning it, templates and version history.

A mixin of ui.workflow_designer.WorkflowDesigner: it relies on the widgets that class builds.
"""
from __future__ import annotations

import logging

from PyQt6.QtWidgets import QInputDialog, QMessageBox

from projects.config_overlay import LOCAL_ONLY_KEYS
from projects.profile_manager import ProjectConfigError
from separation.rule_engine import RuleEngine, RuleSetError

from ui.designer_common import StoredFileUnreadable, UNREADABLE_BANNER, WRITE_FAILED_TEXT, validation_text

log = logging.getLogger("ui.workflow_designer")      # the same logger as before the split


class SaveMixin:
    def _refuse_if_managed(self) -> bool:
        """True (after telling the user) when the shown project is managed
        by the central server: used for actions that would change
        central-owned content (a rollback restores an old central config)."""
        if not self._is_managed():
            return False
        QMessageBox.warning(
            self, "Managed centrally",
            "This project is managed by the central server; local changes "
            "would be overwritten at the next sync. Change it in the "
            "central dashboard instead.")
        return True

    def _write_failed(self, exc: OSError) -> None:
        """A project-file write failed (file locked by another program, disk
        error...): tell the user, log only the exception type, change nothing
        in the form. Qt slots must never let an exception escape."""
        log.error("project file write failed: %s", type(exc).__name__)
        QMessageBox.critical(self, "Could not save", WRITE_FAILED_TEXT)

    def _save_local_sections_only(self) -> None:
        """Managed project: persist ONLY scanner/export/delivery. The stored
        config is the base, so nothing edited in a central-owned part of the
        form (rules, ocr, qc, features, name...) can reach disk. The delivery
        on/off flags stay as stored: the shorthand feature toggles that
        collect_config copies them from belong to central.

        Narrow race: if a sync applies a new central version between the
        load and the save below, this save is based on the older central
        content and the next sync restores central's version. Local sections
        are never overwritten by a sync, so nothing local is lost."""
        try:
            stored = self.pm.load(self.config["project_id"])
        except (ProjectConfigError, ValueError):
            # the error text quotes the stored file: never surface it
            raise StoredFileUnreadable from None
        collected = self.collect_config()
        for key in LOCAL_ONLY_KEYS:
            stored[key] = collected[key]
        self.config = self.pm.save(stored)
        self._bind_config()
        self.refresh_projects_keep_selection()
        QMessageBox.information(
            self, "Saved",
            "Saved this PC's scanner / export / delivery settings. "
            "Everything else is managed centrally and was not changed.")

    def _warn_unreadable(self) -> None:
        QMessageBox.warning(self, "Project unreadable", UNREADABLE_BANNER + ".")

    def save(self) -> None:
        if self.config is None:
            return
        if self._is_managed():
            try:
                self._save_local_sections_only()
            except StoredFileUnreadable:
                self._warn_unreadable()
            except ProjectConfigError as e:     # the config being saved
                QMessageBox.critical(self, "Invalid configuration",
                                     validation_text(e))
            except ValueError:            # the stored file is not valid JSON
                self._warn_unreadable()
            except OSError as e:
                self._write_failed(e)
            return
        try:
            cfg = self.collect_config()
            RuleEngine(cfg["rules"])          # validate DSL before saving
            self.config = self.pm.save(cfg)   # version++ & archive
            self._bind_config()
            self.refresh_projects_keep_selection()
            QMessageBox.information(
                self, "Saved", f"Saved as version {self.config['version']}.")
        except ProjectConfigError as e:
            QMessageBox.critical(self, "Invalid configuration",
                                 validation_text(e))
        except RuleSetError as e:   # the operator's own rule definitions
            QMessageBox.critical(self, "Invalid configuration", str(e))
        except ValueError:          # the stored file went bad after loading
            self._warn_unreadable()
        except OSError as e:
            self._write_failed(e)

    def refresh_projects_keep_selection(self) -> None:
        current = self.project_combo.currentData()
        self.refresh_projects()
        idx = self.project_combo.findData(current)
        if idx >= 0:
            self.project_combo.setCurrentIndex(idx)

    def clone_project(self) -> None:
        if self.config is None:
            return
        name, ok = QInputDialog.getText(self, "Clone project",
                                        "New project name:")
        if ok and name.strip():
            try:
                self.pm.clone(self.config["project_id"], name.strip())
            except OSError as e:
                self._write_failed(e)
                return
            except (ProjectConfigError, ValueError):   # source file unreadable
                self._warn_unreadable()
                return
            self.refresh_projects()

    def new_from_template(self) -> None:
        from projects.template_manager import TemplateManager
        tm = TemplateManager(self.db)
        templates = tm.list_templates()
        if not templates:
            QMessageBox.information(self, "No templates",
                                    "No project templates available yet. "
                                    "Save a configured project as a "
                                    "template first, or check that "
                                    "built-in templates seeded correctly.")
            return
        labels = [f"{t['category']} — {t['name']}" for t in templates]
        label, ok = QInputDialog.getItem(
            self, "New from template", "Template:", labels, 0, False)
        if not ok:
            return
        template = templates[labels.index(label)]
        name, ok = QInputDialog.getText(self, "New project",
                                        "New project name:")
        if not (ok and name.strip()):
            return
        try:
            cfg = tm.build_project_config(template["template_id"])
        except ValueError:                  # template went unreadable meanwhile
            QMessageBox.warning(self, "Template unreadable",
                                "This template could not be read.")
            return
        try:
            new_cfg = self.pm.create(name.strip(), config=cfg)
        except OSError as e:
            self._write_failed(e)
            return
        self.refresh_projects()
        idx = self.project_combo.findData(new_cfg["project_id"])
        if idx >= 0:
            self.project_combo.setCurrentIndex(idx)

    def save_as_template(self) -> None:
        if self.config is None:
            return
        name, ok = QInputDialog.getText(self, "Save as template",
                                        "Template name:")
        if not (ok and name.strip()):
            return
        category, ok = QInputDialog.getText(self, "Save as template",
                                            "Category:")
        if not (ok and category.strip()):
            return
        from projects.template_manager import TemplateManager
        cfg = self.collect_config()
        TemplateManager(self.db).save_as_template(
            cfg, name.strip(), category.strip())
        QMessageBox.information(self, "Saved",
                                f"Saved as template “{name.strip()}”.")

    def rollback_dialog(self) -> None:
        if self.config is None or self._refuse_if_managed():
            return
        versions = self.pm.list_versions(self.config["project_id"])
        if not versions:
            QMessageBox.information(self, "Version history",
                                    "No archived versions yet.")
            return
        version, ok = QInputDialog.getItem(
            self, "Rollback", "Restore version:",
            [str(v) for v in versions], len(versions) - 1, False)
        if ok:
            try:
                restored = self.pm.rollback(self.config["project_id"],
                                            int(version))
            except OSError as e:
                self._write_failed(e)
                return
            except (ProjectConfigError, ValueError):   # a file is unreadable
                self._warn_unreadable()
                return
            self.config = restored
            self._bind_config()
            self.refresh_projects_keep_selection()
