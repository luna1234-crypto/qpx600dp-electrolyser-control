"""Versioned, offline Custom Sequence template serialization."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import tempfile
from typing import Any

from qpx_electrolyser.engine.custom_sequence import SequenceStage, SequenceStageType


TEMPLATE_SCHEMA = "qpx600dp-custom-sequence-v1"


class SequenceTemplateError(ValueError):
    """A sequence template is malformed or cannot be written."""


@dataclass(frozen=True, slots=True)
class SequenceTemplate:
    name: str
    notes: str
    transition_policy: str
    handoff_settling_s: str
    stages: tuple[SequenceStage, ...]

    def to_object(self) -> dict[str, Any]:
        return {
            "schema": TEMPLATE_SCHEMA,
            "template_name": self.name,
            "notes": self.notes,
            "transition_policy": self.transition_policy,
            "handoff_settling_s": self.handoff_settling_s,
            "stages": [
                {
                    "type": stage.stage_type.value,
                    "parameters": dict(stage.parameters),
                }
                for stage in self.stages
            ],
        }


def load_sequence_template(path: Path) -> SequenceTemplate:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("schema") != TEMPLATE_SCHEMA:
            raise SequenceTemplateError("Unsupported sequence-template schema.")
        raw_stages = payload.get("stages")
        if not isinstance(raw_stages, list) or not raw_stages:
            raise SequenceTemplateError("Template must contain at least one stage.")
        stages: list[SequenceStage] = []
        for item in raw_stages:
            if not isinstance(item, dict) or not isinstance(item.get("parameters"), dict):
                raise SequenceTemplateError("A template stage is malformed.")
            stages.append(SequenceStage.create(
                SequenceStageType(str(item["type"])),
                **{str(key): str(value) for key, value in item["parameters"].items()},
            ))
        return SequenceTemplate(
            name=str(payload.get("template_name", "")).strip(),
            notes=str(payload.get("notes", "")),
            transition_policy=str(payload.get("transition_policy", "safe_off")),
            handoff_settling_s=str(payload.get("handoff_settling_s", "1.0")),
            stages=tuple(stages),
        )
    except SequenceTemplateError:
        raise
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise SequenceTemplateError(f"Cannot load sequence template: {error}") from error


def save_sequence_template(path: Path, template: SequenceTemplate) -> None:
    path = Path(path)
    if not path.name.lower().endswith(".qpxseq.txt"):
        path = path.with_name(path.name + ".qpxseq.txt")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", newline="\n", delete=False,
            dir=path.parent, prefix=f".{path.name}.", suffix=".tmp",
        ) as stream:
            temporary = Path(stream.name)
            json.dump(template.to_object(), stream, indent=2, ensure_ascii=False)
            stream.write("\n"); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError as error:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise SequenceTemplateError(f"Cannot save sequence template: {error}") from error

