"""Shared mutation classification for Adventure documents.

SPDX-License-Identifier: GPL-3.0-or-later
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable


class MutationKind(str, Enum):
    """Describe what changed rather than which widget initiated it."""

    PRESENTATION = "presentation"
    VISIBILITY = "visibility"
    METADATA = "metadata"
    ORDER = "order"
    TIMING = "timing"
    ELEVATION = "elevation"
    GEOMETRY = "geometry"
    STRUCTURE = "structure"
    CONTROL = "control"
    ADVENTURE = "adventure"
    AUDIO = "audio"


@dataclass(frozen=True)
class MutationImpact:
    refresh_table: bool = False
    redraw_vectors: bool = False
    refresh_derived_data: bool = False
    mark_maps_stale: bool = False
    changes_gpx_identity: bool = False


MUTATION_IMPACTS = {
    MutationKind.PRESENTATION: MutationImpact(redraw_vectors=True),
    MutationKind.VISIBILITY: MutationImpact(refresh_table=True, redraw_vectors=True),
    MutationKind.METADATA: MutationImpact(refresh_table=True, changes_gpx_identity=True),
    MutationKind.ORDER: MutationImpact(refresh_table=True, redraw_vectors=True, changes_gpx_identity=True),
    MutationKind.TIMING: MutationImpact(
        refresh_table=True,
        redraw_vectors=True,
        refresh_derived_data=True,
        changes_gpx_identity=True,
    ),
    MutationKind.ELEVATION: MutationImpact(
        refresh_table=True,
        redraw_vectors=True,
        refresh_derived_data=True,
        changes_gpx_identity=True,
    ),
    MutationKind.GEOMETRY: MutationImpact(
        refresh_table=True,
        redraw_vectors=True,
        refresh_derived_data=True,
        mark_maps_stale=True,
        changes_gpx_identity=True,
    ),
    MutationKind.STRUCTURE: MutationImpact(
        refresh_table=True,
        redraw_vectors=True,
        refresh_derived_data=True,
        mark_maps_stale=True,
        changes_gpx_identity=True,
    ),
    MutationKind.CONTROL: MutationImpact(),
    MutationKind.ADVENTURE: MutationImpact(),
    MutationKind.AUDIO: MutationImpact(),
}


def normalize_mutation_kind(value) -> MutationKind:
    if isinstance(value, MutationKind):
        return value
    try:
        return MutationKind(str(value or "").strip().casefold())
    except ValueError:
        return MutationKind.GEOMETRY


def impact_for_kind(value) -> MutationImpact:
    return MUTATION_IMPACTS[normalize_mutation_kind(value)]


@dataclass(frozen=True)
class WorkspaceMutation:
    component: str
    kind: MutationKind
    track_numbers: tuple[int, ...] = ()
    message: str = ""
    generation: int = 0

    @classmethod
    def create(
        cls,
        component: str,
        kind,
        *,
        track_numbers: Iterable[int] = (),
        message: str = "",
        generation: int = 0,
    ):
        return cls(
            str(component or "workspace"),
            normalize_mutation_kind(kind),
            tuple(dict.fromkeys(int(number) for number in track_numbers)),
            str(message or ""),
            int(generation),
        )


class MutationJournal:
    """Coalesce unsaved mutations while preserving their required impacts."""

    def __init__(self):
        self._generation = 0
        self._pending: list[WorkspaceMutation] = []

    @property
    def generation(self) -> int:
        return self._generation

    def record(self, component, kind, *, track_numbers=(), message="") -> WorkspaceMutation:
        self._generation += 1
        mutation = WorkspaceMutation.create(
            component,
            kind,
            track_numbers=track_numbers,
            message=message,
            generation=self._generation,
        )
        self._pending.append(mutation)
        return mutation

    def pending(self, *, through_generation: int | None = None) -> tuple[WorkspaceMutation, ...]:
        if through_generation is None:
            return tuple(self._pending)
        return tuple(item for item in self._pending if item.generation <= through_generation)

    def acknowledge(self, through_generation: int):
        self._pending = [
            item for item in self._pending if item.generation > int(through_generation)
        ]

    def acknowledge_component(self, component: str, through_generation: int):
        name = str(component)
        self._pending = [
            item
            for item in self._pending
            if not (
                item.component == name
                and item.generation <= int(through_generation)
            )
        ]

    def has_component_after(self, component: str, generation: int) -> bool:
        name = str(component)
        return any(
            item.component == name and item.generation > int(generation)
            for item in self._pending
        )

    def clear(self):
        self._pending.clear()

    def needs_derived_data(self, *, through_generation: int | None = None) -> bool:
        return any(
            impact_for_kind(item.kind).refresh_derived_data
            for item in self.pending(through_generation=through_generation)
        )

    def changes_gpx_identity(self, *, through_generation: int | None = None) -> bool:
        return any(
            item.component == "gpx" and impact_for_kind(item.kind).changes_gpx_identity
            for item in self.pending(through_generation=through_generation)
        )
