import unittest

from workspace_mutations import (
    MutationJournal,
    MutationKind,
    WorkspaceMutation,
    impact_for_kind,
)


class WorkspaceMutationTests(unittest.TestCase):
    def test_visibility_is_visual_but_does_not_rebuild_derived_data(self):
        impact = impact_for_kind(MutationKind.VISIBILITY)

        self.assertTrue(impact.refresh_table)
        self.assertTrue(impact.redraw_vectors)
        self.assertFalse(impact.refresh_derived_data)
        self.assertFalse(impact.mark_maps_stale)
        self.assertFalse(impact.changes_gpx_identity)

    def test_timing_and_geometry_have_distinct_map_impacts(self):
        timing = impact_for_kind(MutationKind.TIMING)
        geometry = impact_for_kind(MutationKind.GEOMETRY)

        self.assertTrue(timing.refresh_derived_data)
        self.assertFalse(timing.mark_maps_stale)
        self.assertTrue(geometry.refresh_derived_data)
        self.assertTrue(geometry.mark_maps_stale)

    def test_journal_acknowledges_one_component_without_losing_others(self):
        journal = MutationJournal()
        first = journal.record("gpx", MutationKind.VISIBILITY, track_numbers=[2])
        journal.record("control", MutationKind.CONTROL)
        journal.record("gpx", MutationKind.TIMING, track_numbers=[4])

        journal.acknowledge_component("gpx", first.generation)

        pending = journal.pending()
        self.assertEqual([item.component for item in pending], ["control", "gpx"])
        self.assertTrue(journal.needs_derived_data())

    def test_workspace_mutation_deduplicates_track_numbers(self):
        mutation = WorkspaceMutation.create(
            "gpx", "visibility", track_numbers=[3, 3, 1]
        )

        self.assertEqual(mutation.kind, MutationKind.VISIBILITY)
        self.assertEqual(mutation.track_numbers, (3, 1))


if __name__ == "__main__":
    unittest.main()
