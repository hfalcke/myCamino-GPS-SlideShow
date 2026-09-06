# SPDX-License-Identifier: GPL-3.0-or-later
"""Resource and preference helpers for the packaged guided demo."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile


DEMO_RELATIVE_PATH = Path("demo") / "St-Jean-Pied-de-Port-to-Roncesvalles"
DEMO_FORMAT_VERSION = 2
DEMO_PREPARED_DIRECTORY = "3 - Prepared Adventure"
DEMO_PREPARED_PROJECT_NAME = "St Jean to Roncesvalles Demo"


def demo_resource_directory(root=None) -> Path:
    base = Path(root) if root is not None else Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / DEMO_RELATIVE_PATH


def verify_demo_manifest(directory: Path) -> list[str]:
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if int(manifest.get("format_version", 0)) != DEMO_FORMAT_VERSION:
        raise ValueError("Unsupported demo manifest version")
    failures = []
    for entry in manifest.get("files", []):
        path = directory / entry["path"]
        if not path.is_file():
            failures.append(f"Missing {entry['path']}")
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != entry.get("sha256"):
            failures.append(f"Checksum mismatch for {entry['path']}")
        if path.suffix.casefold() in {".jpg", ".jpeg"} and (
            entry.get("visible_content") is not True
            or int(entry.get("orientation", 0) or 0) != 1
        ):
            failures.append(f"Unverified demo image content for {entry['path']}")
    return failures


def create_demo_session(resource_directory: Path, destination_root=None):
    root = Path(tempfile.mkdtemp(prefix="mycamino-demo-", dir=destination_root))
    inputs = root / "Demo Inputs"
    workspace = root / "Demo Adventure"
    shutil.copytree(resource_directory, inputs)
    workspace.mkdir()
    return root, inputs, workspace


def install_demo_prepared_assets(
    inputs_directory: Path,
    workspace_directory: Path,
    project_name: str,
) -> dict[str, int]:
    """Install verified offline sidecars and maps into a disposable demo Adventure."""
    inputs = Path(inputs_directory)
    workspace = Path(workspace_directory)
    prepared = inputs / DEMO_PREPARED_DIRECTORY
    metadata = prepared / "metadata"
    maps = prepared / "trackimages"
    if not metadata.is_dir() or not maps.is_dir():
        raise FileNotFoundError("The bundled offline demo metadata or maps are missing")
    copied_sidecars = 0
    for source in sorted(metadata.glob("*.json")):
        shutil.copy2(source, workspace / source.name)
        copied_sidecars += 1
    destination_maps = workspace / "trackimages"
    destination_maps.mkdir(parents=True, exist_ok=True)
    target_name = str(project_name).strip()
    if not target_name:
        raise ValueError("The destination Adventure name is required")

    def renamed_relative_path(source: Path) -> Path:
        relative = source.relative_to(maps)
        return Path(*(
            part.replace(DEMO_PREPARED_PROJECT_NAME, target_name)
            for part in relative.parts
        ))

    copied_maps = 0
    for source in sorted(maps.rglob("*")):
        if not source.is_file():
            continue
        target = destination_maps / renamed_relative_path(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.suffix.casefold() == ".json" and target_name != DEMO_PREPARED_PROJECT_NAME:
            text = source.read_text(encoding="utf-8").replace(
                DEMO_PREPARED_PROJECT_NAME,
                target_name,
            )
            target.write_text(text, encoding="utf-8")
        else:
            shutil.copy2(source, target)
        copied_maps += 1
    return {"sidecars": copied_sidecars, "map_assets": copied_maps}


def demo_prepared_render_parameters(inputs_directory: Path) -> dict:
    """Return the rendering settings used to create the bundled Standard map."""
    maps = Path(inputs_directory) / DEMO_PREPARED_DIRECTORY / "trackimages"
    candidates = sorted(
        path for path in maps.glob("0001_*.json") if not path.stem.endswith("-timelapse")
    )
    if not candidates:
        return {}
    payload = json.loads(candidates[0].read_text(encoding="utf-8"))
    values = payload.get("adventure_render_parameters")
    return dict(values) if isinstance(values, dict) else {}
