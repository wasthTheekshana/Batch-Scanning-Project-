"""Building the designer's form: the header and one group box per kind of setting.

A mixin of ui.workflow_designer.WorkflowDesigner: it relies on the widgets that class builds.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QCheckBox, QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget

from ui.designer_common import BARCODE_ROLES, CENTRAL_UPDATED_NOTICE, EXPORT_FORMATS, INDEX_FORMATS, MANAGED_BANNER, QC_PRESETS, QC_STAGES, SAVE_LABEL, STRATEGIES, UNREADABLE_BANNER


class FormBuilderMixin:
    def _build_header(self) -> QVBoxLayout:
        """The project selector, its buttons, the banners and the scrolling form; returns the form's layout."""
        outer = QVBoxLayout(self)

        top = QHBoxLayout()
        self.project_combo = QComboBox()
        self.project_combo.currentIndexChanged.connect(self.load_selected)
        top.addWidget(QLabel("Project:"))
        self.project_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.project_combo.setMinimumContentsLength(12)
        top.addWidget(self.project_combo, stretch=1)
        actions = QHBoxLayout()      # second row: seven buttons in one row
        actions.addStretch(1)        # made the tab 1276 px wide
        self.clone_btn = QPushButton("Clone…")
        self.clone_btn.clicked.connect(self.clone_project)
        self.new_from_template_btn = QPushButton("New from template…")
        self.new_from_template_btn.clicked.connect(self.new_from_template)
        self.rollback_btn = QPushButton("Version history…")
        self.rollback_btn.clicked.connect(self.rollback_dialog)
        self.save_btn = QPushButton(SAVE_LABEL)
        self.save_btn.clicked.connect(self.save)
        self.save_template_btn = QPushButton("Save as template…")
        self.save_template_btn.clicked.connect(self.save_as_template)
        from ui.theme import mark
        mark(self.save_btn, "primary")
        for b in (self.new_from_template_btn, self.clone_btn,
                  self.rollback_btn, self.save_btn, self.save_template_btn):
            b.setEnabled(self.can_edit)
            actions.addWidget(b)
        outer.addLayout(top)
        outer.addLayout(actions)

        self.version_label = QLabel("")
        outer.addWidget(self.version_label)
        self.managed_banner = QLabel(MANAGED_BANNER)
        self.managed_banner.setWordWrap(True)
        self.managed_banner.setTextFormat(Qt.TextFormat.PlainText)
        self.managed_banner.hide()
        outer.addWidget(self.managed_banner)
        self.unreadable_banner = QLabel(UNREADABLE_BANNER)
        self.unreadable_banner.setWordWrap(True)
        self.unreadable_banner.setTextFormat(Qt.TextFormat.PlainText)
        self.unreadable_banner.hide()
        outer.addWidget(self.unreadable_banner)
        self.central_notice = QLabel(CENTRAL_UPDATED_NOTICE)
        self.central_notice.setWordWrap(True)
        self.central_notice.setTextFormat(Qt.TextFormat.PlainText)
        self.central_notice.hide()
        outer.addWidget(self.central_notice)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        scroll.setWidget(body)
        self.form_body = body
        outer.addWidget(scroll, stretch=1)
        return QVBoxLayout(body)

    def _build_feature_box(self, layout) -> None:
        """Which features are on."""
        feature_box = self.feature_box = QGroupBox("Feature toggles")
        f_layout = QVBoxLayout(feature_box)
        self.feature_checks: dict[str, QCheckBox] = {}
        for key in ("auto_index", "duplicate_check", "sftp", "cd_burn"):
            cb = QCheckBox(key)
            cb.setEnabled(self.can_edit)
            self.feature_checks[key] = cb
            f_layout.addWidget(cb)
        layout.addWidget(feature_box)

    def _build_separation_box(self, layout) -> None:
        """How a stack is cut into documents."""
        sep_box = self.sep_box = QGroupBox("Separation strategy")
        s_layout = QVBoxLayout(sep_box)
        self.strategy_checks: dict[str, QCheckBox] = {}
        for key in STRATEGIES:
            cb = QCheckBox(key)
            cb.setEnabled(self.can_edit)
            self.strategy_checks[key] = cb
            s_layout.addWidget(cb)
        fixed_row = QFormLayout()
        self.fixed_pages = QSpinBox()
        self.fixed_pages.setRange(0, 999)
        self.fixed_pages.setSpecialValueText("(off)")
        self.fixed_pages.setEnabled(self.can_edit)
        fixed_row.addRow("Fixed pages per document", self.fixed_pages)
        self.drop_blanks = QCheckBox(
            "Discard blank pages (the empty back of a one-sided sheet)")
        self.drop_blanks.setEnabled(self.can_edit)
        fixed_row.addRow(self.drop_blanks)
        self.barcode_role = QComboBox()
        for label, key in BARCODE_ROLES:
            self.barcode_role.addItem(label, key)
        self.barcode_role.setEnabled(self.can_edit)
        fixed_row.addRow("A page with a barcode is", self.barcode_role)
        self.barcode_pattern = QLineEdit()
        self.barcode_pattern.setPlaceholderText(
            r"optional, e.g. ^CIF-\d+$ (other barcodes are ignored)")
        self.barcode_pattern.setEnabled(self.can_edit)
        fixed_row.addRow("Only barcodes matching", self.barcode_pattern)
        s_layout.addLayout(fixed_row)
        layout.addWidget(sep_box)

    def _build_qc_box(self, layout) -> None:
        """The quality-check settings."""
        qc_box = self.qc_box = QGroupBox("QC pipeline")
        q_layout = QFormLayout(qc_box)
        self.qc_preset = QComboBox()
        self.qc_preset.addItems(["Custom", *QC_PRESETS.keys()])
        self.qc_preset.setEnabled(self.can_edit)
        self.qc_preset.currentTextChanged.connect(self._apply_qc_preset)
        q_layout.addRow("Strictness preset", self.qc_preset)
        self.qc_enabled = QCheckBox("QC enabled")
        self.qc_enabled.setEnabled(self.can_edit)
        q_layout.addRow(self.qc_enabled)
        self.qc_stage_checks: dict[str, QCheckBox] = {}
        for stage in QC_STAGES:
            cb = QCheckBox(stage)
            cb.setEnabled(self.can_edit)
            self.qc_stage_checks[stage] = cb
            q_layout.addRow(cb)
        self.pass_score = QSpinBox()
        self.pass_score.setRange(0, 100)
        self.blur_warn = QSpinBox()
        self.blur_warn.setRange(0, 10000)
        self.blur_fail = QSpinBox()
        self.blur_fail.setRange(0, 10000)
        self.dpi_warn = QSpinBox()
        self.dpi_warn.setRange(0, 2400)
        self.dpi_fail = QSpinBox()
        self.dpi_fail.setRange(0, 2400)
        for w in (self.pass_score, self.blur_warn, self.blur_fail,
                  self.dpi_warn, self.dpi_fail):
            w.setEnabled(self.can_edit)
        q_layout.addRow("Pass score ≥", self.pass_score)
        q_layout.addRow("Blur WARN threshold", self.blur_warn)
        q_layout.addRow("Blur FAIL threshold", self.blur_fail)
        q_layout.addRow("DPI WARN threshold", self.dpi_warn)
        q_layout.addRow("DPI FAIL threshold", self.dpi_fail)
        layout.addWidget(qc_box)

    def _build_export_box(self, layout) -> None:
        """Scanner, export and index-file settings."""
        exp_box = self.exp_box = QGroupBox("Scanner & export")
        e_layout = QFormLayout(exp_box)
        self.dpi = QSpinBox()
        self.dpi.setRange(75, 1200)
        self.color = QComboBox()
        self.color.addItems(["bw", "grayscale", "color"])
        self.export_format = QComboBox()
        self.export_format.addItems(EXPORT_FORMATS)
        self.filename_template = QLineEdit()
        self.output_path = QLineEdit()
        self.delivery_path = QLineEdit()
        self.hot_enabled = QCheckBox(
            "Watch a folder for files dropped by a network scanner")
        self.hot_path = QLineEdit()
        self.hot_path.setPlaceholderText(
            "the folder the scanner saves into (handled files move to processed or failed)")
        self.hot_enabled.toggled.connect(self.hot_path.setEnabled)
        for w in (self.dpi, self.color, self.export_format,
                  self.filename_template, self.output_path,
                  self.delivery_path, self.hot_enabled, self.hot_path):
            w.setEnabled(self.can_edit)
        e_layout.addRow("Scan DPI", self.dpi)
        e_layout.addRow("Hot folder", self.hot_enabled)
        e_layout.addRow("Hot folder path", self.hot_path)
        e_layout.addRow("Colour mode", self.color)
        e_layout.addRow("Export format", self.export_format)
        e_layout.addRow("Filename template", self.filename_template)
        e_layout.addRow("Export path", self.output_path)
        e_layout.addRow("Delivery folder", self.delivery_path)
        self.index_enabled = QCheckBox(
            "Write an index file with each document (the index fields)")
        self.index_format = QComboBox()
        for label, key in INDEX_FORMATS:
            self.index_format.addItem(label, key)
        self.index_running = QCheckBox(
            "Also keep one running index.csv of every document")
        for w in (self.index_enabled, self.index_format, self.index_running):
            w.setEnabled(self.can_edit)
        self.index_enabled.toggled.connect(self._index_toggled)
        e_layout.addRow(self.index_enabled)
        e_layout.addRow("Index file format", self.index_format)
        e_layout.addRow(self.index_running)
        self._index_toggled(False)
        layout.addWidget(exp_box)

    def _build_zones_box(self, layout) -> None:
        """The index zones (fields read from every page)."""
        zone_box = self.zone_box = QGroupBox("Index zones (auto-index fields)")
        z_layout = QVBoxLayout(zone_box)
        z_layout.addWidget(QLabel(
            "Each zone reads one field from the page. Zone-based: drag a "
            "fixed region per field. Full-page: OCR the whole page and "
            "match each field by a label-anchored regex — use this when "
            "field position varies between documents in this project."))
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Extraction mode"))
        self.ocr_mode = QComboBox()
        self.ocr_mode.addItems(["Zone-based (draw regions)",
                                "Full-page (label-anchored regex)"])
        self.ocr_mode.setEnabled(self.can_edit)
        self.ocr_mode.currentIndexChanged.connect(self._relabel_zones)
        mode_row.addWidget(self.ocr_mode)
        mode_row.addStretch(1)
        z_layout.addLayout(mode_row)
        self.zone_list = QListWidget()
        z_layout.addWidget(self.zone_list)
        zone_btns = QHBoxLayout()
        self.add_zone_btn = QPushButton("Add zone…")
        self.edit_zone_btn = QPushButton("Edit…")
        self.del_zone_btn = QPushButton("Remove")
        self.add_zone_btn.clicked.connect(self.add_zone)
        self.edit_zone_btn.clicked.connect(self.edit_zone)
        self.del_zone_btn.clicked.connect(
            lambda: self.zone_list.takeItem(self.zone_list.currentRow()))
        for b in (self.add_zone_btn, self.edit_zone_btn, self.del_zone_btn):
            b.setEnabled(self.can_edit)
            zone_btns.addWidget(b)
        z_layout.addLayout(zone_btns)
        layout.addWidget(zone_box)

    def _build_types_box(self, layout) -> None:
        """Document types: one customer file is several kinds of page."""
        type_box = self.type_box = QGroupBox(
            "Document types (separate index fields per kind of document)")
        t_layout = QVBoxLayout(type_box)
        t_layout.addWidget(QLabel(
            "Use when one project scans several kinds of document (NIC, "
            "application, letter...). Each type has a pattern that "
            "recognises its pages and its own index fields; the fields above "
            "apply to every page. The exported PDF gets a bookmark for each "
            "type."))
        self.type_list = QListWidget()
        t_layout.addWidget(self.type_list)
        type_btns = QHBoxLayout()
        self.add_type_btn = QPushButton("Add document type…")
        self.edit_type_btn = QPushButton("Edit…")
        self.del_type_btn = QPushButton("Remove")
        self.add_type_btn.clicked.connect(self.add_type)
        self.edit_type_btn.clicked.connect(self.edit_type)
        self.del_type_btn.clicked.connect(
            lambda: self.type_list.takeItem(self.type_list.currentRow()))
        for b in (self.add_type_btn, self.edit_type_btn, self.del_type_btn):
            b.setEnabled(self.can_edit)
            type_btns.addWidget(b)
        t_layout.addLayout(type_btns)
        layout.addWidget(type_box)

    def _build_sftp_box(self, layout) -> None:
        """SFTP delivery."""
        sftp_box = self.sftp_box = QGroupBox("SFTP delivery (LAN)")
        sftp_layout = QFormLayout(sftp_box)
        self.sftp_enabled = QCheckBox("Enabled")
        self.sftp_enabled.setEnabled(self.can_edit)
        sftp_layout.addRow(self.sftp_enabled)
        self.sftp_host = QLineEdit()
        self.sftp_port = QSpinBox()
        self.sftp_port.setRange(1, 65535)
        self.sftp_port.setValue(22)
        self.sftp_user = QLineEdit()
        self.sftp_password = QLineEdit()
        self.sftp_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.sftp_key_file = QLineEdit()
        self.sftp_remote_path = QLineEdit()
        for w in (self.sftp_host, self.sftp_port, self.sftp_user,
                  self.sftp_password, self.sftp_key_file,
                  self.sftp_remote_path):
            w.setEnabled(self.can_edit)
        sftp_layout.addRow("Host", self.sftp_host)
        sftp_layout.addRow("Port", self.sftp_port)
        sftp_layout.addRow("Username", self.sftp_user)
        sftp_layout.addRow("Password", self.sftp_password)
        sftp_layout.addRow("Key file (optional)", self.sftp_key_file)
        sftp_layout.addRow("Remote path", self.sftp_remote_path)
        sftp_layout.addRow(QLabel("Tick Enabled to deliver via SFTP."))
        layout.addWidget(sftp_box)

    def _build_cd_box(self, layout) -> None:
        """CD/DVD delivery."""
        cd_box = self.cd_box = QGroupBox("CD/DVD delivery")
        cd_layout = QVBoxLayout(cd_box)
        self.cd_enabled = QCheckBox("Enabled")
        self.cd_enabled.setEnabled(self.can_edit)
        cd_layout.addWidget(self.cd_enabled)
        cd_layout.addWidget(QLabel("Tick Enabled to deliver to CD/DVD."))
        self.cd_hint = QLabel("Only the startup project's disc queue can be "
                              "burned from Admin; enable this on one project "
                              "only.")
        self.cd_hint.setTextFormat(Qt.TextFormat.PlainText)
        self.cd_hint.setWordWrap(True)
        cd_layout.addWidget(self.cd_hint)
        layout.addWidget(cd_box)

    def _build_rules_box(self, layout) -> None:
        """The IF / THEN rules."""
        rule_box = self.rule_box = QGroupBox("Rules (IF / THEN)")
        r_layout = QVBoxLayout(rule_box)
        self.rule_list = QListWidget()
        r_layout.addWidget(self.rule_list)
        rule_btns = QHBoxLayout()
        self.add_rule_btn = QPushButton("Add rule…")
        self.edit_rule_btn = QPushButton("Edit…")
        self.del_rule_btn = QPushButton("Delete")
        self.add_rule_btn.clicked.connect(self.add_rule)
        self.edit_rule_btn.clicked.connect(self.edit_rule)
        self.del_rule_btn.clicked.connect(self.delete_rule)
        for b in (self.add_rule_btn, self.edit_rule_btn, self.del_rule_btn):
            b.setEnabled(self.can_edit)
            rule_btns.addWidget(b)
        r_layout.addLayout(rule_btns)
        layout.addWidget(rule_box)

    def _finish_form(self) -> None:
        """Let long labels and combo boxes wrap, so no help text sets the form's minimum width."""
        # A long help text that cannot wrap sets the minimum width of the whole
        # form (it was 3243 px): let every long label wrap.
        for lbl in self.form_body.findChildren(QLabel):
            if len(lbl.text()) > 60:
                lbl.setWordWrap(True)
        for combo in self.form_body.findChildren(QComboBox):
            combo.setSizeAdjustPolicy(
                QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(14)
