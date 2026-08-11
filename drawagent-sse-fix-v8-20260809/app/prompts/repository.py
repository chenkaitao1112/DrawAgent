from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class SafeFormatDict(dict[str, Any]):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


@dataclass(frozen=True)
class PromptEntry:
    name: str
    file_path: Path
    description: str


class PromptRepository:
    def __init__(self, root_dir: Path, manifest_path: Path) -> None:
        self.root_dir = root_dir
        self.manifest_path = manifest_path
        self._manifest = self._load_manifest()

    def _load_manifest(self) -> dict[str, PromptEntry]:
        with self.manifest_path.open("r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle) or {}
        manifest: dict[str, PromptEntry] = {}
        for name, config in (raw.get("agents") or {}).items():
            manifest[name] = PromptEntry(
                name=name,
                file_path=self.root_dir / Path(config["file"]),
                description=str(config.get("description", "")),
            )
        return manifest

    def render(self, name: str, context: dict[str, Any] | None = None) -> str:
        text = self._manifest[name].file_path.read_text(encoding="utf-8")
        return text.format_map(SafeFormatDict(context or {}))

