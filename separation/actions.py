"""Rule-engine action implementations (HLD §8.3).

Each DSL action type is a BaseRuleAction subclass registered in
ACTION_REGISTRY. New actions drop in without touching the engine.
"""
from __future__ import annotations

from typing import Any

from separation.base import BaseRuleAction


class SetFolderAction(BaseRuleAction):
    @property
    def action_type(self) -> str:
        return "set_folder"

    def apply(self, document: dict, value: Any) -> None:
        document["output_folder"] = str(value)


class SetFilenameAction(BaseRuleAction):
    @property
    def action_type(self) -> str:
        return "set_filename"

    def apply(self, document: dict, value: Any) -> None:
        document["filename_template"] = str(value)


class SetValidationStatusAction(BaseRuleAction):
    ALLOWED = {"pending_qc", "auto_approved", "corrected", "rejected"}

    @property
    def action_type(self) -> str:
        return "set_validation_status"

    def apply(self, document: dict, value: Any) -> None:
        if value not in self.ALLOWED:
            raise ValueError(f"invalid validation_status: {value}")
        document["validation_status"] = value


class AddFlagAction(BaseRuleAction):
    @property
    def action_type(self) -> str:
        return "add_flag"

    def apply(self, document: dict, value: Any) -> None:
        flags = document.setdefault("flags", [])
        if value not in flags:
            flags.append(value)


class SetMetadataFieldAction(BaseRuleAction):
    @property
    def action_type(self) -> str:
        return "set_metadata_field"

    def apply(self, document: dict, value: Any) -> None:
        # value: {"field": "<key>", "value": <any>}
        document.setdefault("extracted_fields", {})[value["field"]] = value["value"]


class TriggerDeliveryAction(BaseRuleAction):
    @property
    def action_type(self) -> str:
        return "trigger_delivery"

    def apply(self, document: dict, value: Any) -> None:
        channels = document.setdefault("trigger_channels", [])
        if value not in channels:
            channels.append(value)


class SkipStageAction(BaseRuleAction):
    @property
    def action_type(self) -> str:
        return "skip_stage"

    def apply(self, document: dict, value: Any) -> None:
        skips = document.setdefault("skip_stages", [])
        if value not in skips:
            skips.append(value)


ACTION_REGISTRY: dict[str, BaseRuleAction] = {
    a.action_type: a for a in (
        SetFolderAction(), SetFilenameAction(), SetValidationStatusAction(),
        AddFlagAction(), SetMetadataFieldAction(), TriggerDeliveryAction(),
        SkipStageAction(),
    )
}
