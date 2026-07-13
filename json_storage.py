#!/usr/bin/env python3
"""Small helpers for durable, atomic JSON preference files."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable

from adventure_parameters import (
    PARAMETER_SCHEMA_VERSION,
    SPECS_BY_KEY,
    default_parameters,
    normalize_parameter_value,
)


def atomic_write_json(path: str | os.PathLike, payload: Any) -> Path:
    """Write JSON beside its destination and atomically replace the old file."""
    destination = Path(path).expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
        text=True,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, destination)
    except Exception:
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return destination


def parameter_subset_payload(values: dict[str, Any], keys: Iterable[str]) -> dict[str, Any]:
    """Return a versioned parameter payload containing only the requested keys."""
    selected = {}
    defaults = default_parameters()
    for key in keys:
        spec = SPECS_BY_KEY.get(key)
        if spec is None:
            continue
        try:
            selected[key] = normalize_parameter_value(spec, values.get(key, defaults[key]))
        except (TypeError, ValueError):
            selected[key] = defaults[key]
    return {"version": PARAMETER_SCHEMA_VERSION, "values": selected}


def load_parameter_subset(
    path: str | os.PathLike,
    keys: Iterable[str],
) -> tuple[dict[str, Any], list[str]]:
    """Load selected settings over defaults and report invalid stored values."""
    source = Path(path).expanduser()
    values = default_parameters()
    selected_keys = tuple(key for key in keys if key in SPECS_BY_KEY)
    if not source.exists():
        return values, []
    try:
        with source.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        return values, [f"Could not read {source}: {exc}"]

    raw_values = payload.get("values") if isinstance(payload, dict) else None
    if not isinstance(raw_values, dict):
        return values, [f"Settings file {source} has no valid values section."]

    warnings = []
    for key in selected_keys:
        if key not in raw_values:
            continue
        spec = SPECS_BY_KEY[key]
        try:
            values[key] = normalize_parameter_value(spec, raw_values[key])
        except (TypeError, ValueError) as exc:
            warnings.append(f"{spec.label}: {exc}; using the default value.")
    return values, warnings
