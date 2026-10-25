import copy
import json
import os
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from adventure_parameters import default_parameters, normalize_parameters
from GPXEditor import (GPXEditorController, TrackRecord, qname, get_or_create_mycamino_track_extension,
                       build_selected_tracks_root)
from gpx_processing import processing_input_fingerprint, semantic_track_fingerprint
from gpx_pdf_summary import SummaryOptions, SummarySnapshot, summary_rows, summary_track
from map_overlay import MAP_CONTENT_VERSION, map_uses_dynamic_overlays
from plot_metadata_utils import write_plot_metadata
from track_types import (TYPE_LABELS, TYPE_COLORS, alphabetic_suffix, normalize_track_type,
                         numbering_proposal, report_stage_title, track_color, track_ids)
from track_symbols import symbol_paths
from workspace_mutations import MutationKind


def track(number, stage=None, kind=None, hidden=False):
    element = ET.fromstring(f'<trk xmlns="http://www.topografix.com/GPX/1/1"><name>Track {number}</name>'
                           '<trkseg><trkpt lat="50" lon="7"><ele>100</ele></trkpt></trkseg></trk>')
    result = TrackRecord(number, element, metrics=dict(length_km=1, npoints=1), metrics_dirty=False)
    result.set_order_number(number)
    if stage is not None:
        result.set_stage_number(stage)
    if kind is not None:
        result.set_track_type(kind)
    if hidden:
        result.set_hidden(True)
    return result


class TypeAndStageTests(unittest.TestCase):
    def test_unassigned_and_imported_unknown_remain_unassigned(self):
        item = track(1)
        self.assertEqual(item.track_type, "unassigned")
        ET.SubElement(item.element, qname("type")).text = "Canoeing - keep me"
        serialized = ET.tostring(item.element)
        self.assertEqual(item.track_type, "unassigned")
        self.assertEqual(ET.tostring(item.element), serialized)
        item.set_track_type("hiking")
        self.assertEqual(item.element.findtext(qname("type")), "hiking")

    def test_supported_types_aliases_and_serialization(self):
        for kind in TYPE_LABELS:
            item = track(1, stage=3, kind=kind)
            restored = TrackRecord(1, ET.fromstring(ET.tostring(item.element)))
            self.assertEqual(restored.track_type, kind)
            self.assertEqual(restored.stage_number, 3)
        for alias, expected in (("Walking", "hiking"), ("bicycle", "bike"), ("plane", "airplane")):
            self.assertEqual(normalize_track_type(alias), expected)

    def test_new_track_setting_does_not_change_imported_tracks(self):
        settings = default_parameters()
        self.assertEqual(settings["track_types.new_track_default"], "pilgrimage")
        self.assertEqual(normalize_parameters({})["timelapse.marker_source"], "track_type")
        self.assertEqual(track(1).track_type, "unassigned")
        controller = SimpleNamespace(next_nr=1, tracks=[], dirty=False, project_name="Test",
            project_parameters=settings, recalculate=Mock(), update_selection_field=Mock(),
            highlight_selected_rows=Mock(), set_status=Mock())
        created = GPXEditorController.create_empty_track(controller, mark_dirty=False)
        self.assertEqual(created.track_type, "pilgrimage")
        settings["track_types.new_track_default"] = "unassigned"
        self.assertEqual(GPXEditorController.create_empty_track(controller, mark_dirty=False).track_type, "unassigned")

    def test_public_transport_settings_and_markers(self):
        from GPSTrackShow import time_lapse_marker_style
        defaults = default_parameters()
        for kind in ("bus", "train", "metro"):
            with self.subTest(kind=kind):
                self.assertEqual(TYPE_LABELS[kind], kind.title())
                self.assertEqual(defaults[f"track_types.{kind}_color"], TYPE_COLORS[kind])
                settings = normalize_parameters({"track_types.new_track_default": kind,
                    f"track_types.{kind}_color": "#123456"})
                self.assertEqual(settings["track_types.new_track_default"], kind)
                self.assertEqual(track_color(track(1, kind=kind), settings), "#123456")
                for overview in (False, True):
                    self.assertEqual(time_lapse_marker_style("car", source="track_type",
                        track_type=kind, overview=overview), kind)

    def test_native_track_menu_label_and_help(self):
        import GPXEditor
        import cocoa_adventure_map
        from cocoa_native_menus import MENU_HELP
        for module, builder in ((GPXEditor, GPXEditor.build_gpx_editor_menu),
                                (cocoa_adventure_map, cocoa_adventure_map.build_adventure_map_menu)):
            with self.subTest(interface=module.__name__), \
                    patch.object(module, "NSMenu"), patch.object(module, "NSMenuItem"), \
                    patch.object(module, "NSApp"), patch.object(module, "add_menu"), \
                    patch.object(module, "install_application_news"), \
                    patch.object(module, "WindowMenuCoordinator"), \
                    patch.object(module, "menu_item") as item:
                builder(Mock())
                entries = [(call.args[0], call.args[1]) for call in item.call_args_list]
                self.assertIn(("Track Table & Editor", "showTrackTable:"), entries)
                self.assertNotIn(("Track Table", "showTrackTable:"), entries)
                self.assertIn(("Invert Selection", "invertTrackSelection:"), entries)
                self.assertNotIn(("Clear Track Numbers", "clearTrackNumbers:"), entries)
                self.assertNotIn(("Clear Stage Numbers", "clearStageNumbers:"), entries)
        self.assertIn("Track Table & Editor", MENU_HELP)

    def test_invert_selection_includes_hidden_without_mutating_documents(self):
        tracks = [track(9), track(2, hidden=True), track(6)]
        before = [ET.tostring(item.element) for item in tracks]
        controller = SimpleNamespace(tracks=tracks, selected_nrs=[9, 6],
            update_selection_field=Mock(), highlight_selected_rows=Mock(),
            redraw_open_plot_views=Mock(), set_status=Mock())
        GPXEditorController.invertTrackSelection_.callable(controller, None)
        self.assertEqual(controller.selected_nrs, [2])
        GPXEditorController.invertTrackSelection_.callable(controller, None)
        self.assertEqual(controller.selected_nrs, [9, 6])
        self.assertEqual([ET.tostring(item.element) for item in tracks], before)
        controller.selected_nrs = [item.nr for item in tracks]
        GPXEditorController.invertTrackSelection_.callable(controller, None)
        self.assertEqual(controller.selected_nrs, [])
        GPXEditorController.invertTrackSelection_.callable(controller, None)
        self.assertEqual(controller.selected_nrs, [9, 2, 6])

    def test_unassigned_numbers_roundtrip_without_changing_identity_or_fingerprints(self):
        item = track(4)
        fingerprint = semantic_track_fingerprint(item.element)
        processing = processing_input_fingerprint(item.element)
        item.set_track_number_assigned(False)
        self.assertEqual(item.stage_number, 4)
        item.set_stage_number(None)
        restored = TrackRecord(4, ET.fromstring(ET.tostring(item.element)))
        self.assertIsNone(restored.track_number)
        self.assertIsNone(restored.stage_number)
        self.assertEqual(restored.internal_id, 4)
        self.assertIsNone(restored.stored_order_number)
        self.assertEqual(semantic_track_fingerprint(item.element), fingerprint)
        self.assertEqual(processing_input_fingerprint(item.element), processing)
        self.assertEqual(track_ids([item]), {4: ""})
        self.assertEqual(report_stage_title(item, [item]), item.name)
        item.set_track_number_assigned(True)
        self.assertEqual(report_stage_title(item, [item]), "Track 4: Track 4")
        item.set_stage_number(7)
        self.assertEqual(track_ids([item]), {4: "7"})

    def test_clear_numbers_is_single_metadata_transaction_and_undoable(self):
        tracks = [track(1, 8), track(2, 8, hidden=True), track(3, 9)]
        for item in tracks:
            item.processed = object()
        geometry = [item.processed for item in tracks]
        controller = SimpleNamespace(tracks=tracks, selected_nrs=[1, 2],
            selected_tracks=lambda: tracks[:2], ensure_track_mutations_available=lambda: True,
            metadata_changed=Mock(), undo_stack=[], redo_stack=[],
            track_metadata_options=Mock(return_value=("stage", "selected", True, None, "clear")),
            confirm_numbering_preview=Mock(return_value=True))
        controller.metadata_snapshot = lambda items, kind=MutationKind.METADATA: GPXEditorController.metadata_snapshot(controller, items, kind)
        controller.push_metadata_undo = lambda items: GPXEditorController.push_metadata_undo(controller, items)
        GPXEditorController.renumberTracks_.callable(controller, None)
        self.assertEqual([item.stage_number for item in tracks], [None, None, 9])
        self.assertEqual(len(controller.undo_stack), 1)
        inverse = GPXEditorController.restore_metadata(controller, controller.undo_stack.pop())
        self.assertEqual([item.stage_number for item in tracks], [8, 8, 9])
        GPXEditorController.restore_metadata(controller, inverse)
        self.assertEqual([item.stage_number for item in tracks], [None, None, 9])
        controller.track_metadata_options.return_value = ("track", "selected", True, None, "clear")
        GPXEditorController.renumberTracks_.callable(controller, None)
        self.assertEqual([item.track_number for item in tracks], [None, None, 3])
        GPXEditorController.restore_metadata(controller, controller.undo_stack.pop())
        self.assertEqual([item.track_number for item in tracks], [1, 2, 3])
        self.assertEqual([item.processed for item in tracks], geometry)
        self.assertEqual(controller.selected_nrs, [1, 2])

    def test_clearing_stage_cell_and_renumbering_restore_assignment(self):
        item = track(1)
        controller = SimpleNamespace(tracks=[item], ensure_track_mutations_available=lambda: True,
            push_metadata_undo=Mock(), metadata_changed=Mock(), set_status=Mock(),
            _rebuild_table_rows_from_cached_metrics=Mock())
        GPXEditorController.edit_table_value(controller, 0, "stage", "")
        self.assertIsNone(item.stage_number)
        controller.push_metadata_undo.assert_called_once_with([item])
        GPXEditorController.edit_table_value(controller, 0, "stage", "5")
        self.assertEqual(item.stage_number, 5)
        GPXEditorController.edit_table_value(controller, 0, "stage", "-1")
        self.assertEqual(item.stage_number, 5)
        item.set_stage_number(None)
        self.assertEqual(numbering_proposal([item, track(2)], [2], mode="stage").start, 1)
        item.set_track_number_assigned(False)
        controller.selected_nrs = [1]
        controller.selected_tracks = lambda: [item]
        controller.track_metadata_options = Mock(return_value=("track", "selected", True, "1", "assign"))
        controller.confirm_numbering_preview = Mock(return_value=True)
        controller.remap_track_number_state = Mock()
        GPXEditorController.renumberTracks_.callable(controller, None)
        self.assertEqual(item.track_number, 1)
        self.assertIsNone(item.stage_number)

    def test_pdf_omits_unassigned_numbers_without_grouping_blank_stages(self):
        tracks = [track(1), track(2), track(3, 7), track(4, 7)]
        for item in tracks[:2]:
            item.set_stage_number(None)
            item.set_track_number_assigned(False)
        ids = track_ids(tracks)
        self.assertEqual(ids, {1: "", 2: "", 3: "7a", 4: "7b"})
        rows = tuple(summary_track(item, index, ids) for index, item in enumerate(tracks, 1))
        snapshot = SummarySnapshot("Test", rows, SummaryOptions(columns=("nr", "stage", "name")))
        self.assertEqual(summary_rows(snapshot)[0].cells[:3], ("", "", ""))
        self.assertEqual(summary_rows(snapshot)[2].cells[:3], ("3", "7", "7a"))
        subset = SummarySnapshot("Test", rows[:2], SummaryOptions(columns=("stage", "name")))
        self.assertEqual(subset.options.columns, ("stage", "name"))

    def test_selected_export_preserves_explicitly_unassigned_numbers(self):
        item = track(4)
        item.set_stage_number(None)
        item.set_track_number_assigned(False)
        root = build_selected_tracks_root([item], Path("/tmp/export.gpx"))
        exported = TrackRecord(1, root.find(qname("trk")))
        self.assertIsNone(exported.stage_number)
        self.assertIsNone(exported.track_number)

    def test_metadata_does_not_change_processing_or_semantic_fingerprint(self):
        item = track(1)
        inputs = processing_input_fingerprint(item.element)
        semantic = semantic_track_fingerprint(item.element)
        item.set_stage_number(9)
        item.set_track_type("horse")
        self.assertEqual(processing_input_fingerprint(item.element), inputs)
        self.assertEqual(semantic_track_fingerprint(item.element), semantic)

    def test_stage_fallback_and_stable_complete_document_ids(self):
        tracks = [track(8, 3), track(2, 3, hidden=True), track(9)]
        self.assertEqual(track_ids(tracks), {2: "3a", 8: "3b", 9: "9"})
        tracks.reverse()
        tracks[1].set_hidden(False)
        self.assertEqual(track_ids(tracks), {2: "3a", 8: "3b", 9: "9"})
        self.assertEqual(tracks[0].stage_number, 9)

    def test_suffixes_beyond_z(self):
        identities = track_ids([track(i, 3) for i in range(1, 55)])
        self.assertEqual([identities[i] for i in (1, 26, 27, 28, 52, 53, 54)],
                         ["3a", "3z", "3aa", "3ab", "3az", "3ba", "3bb"])

    def test_numbering_scope_row_order_and_hidden(self):
        tracks = [track(8), track(2, hidden=True), track(9), track(11)]
        proposal = numbering_proposal(tracks, (8, 9), start=20)
        self.assertEqual(proposal.changes, ((8, 20), (9, 21)))
        self.assertEqual(numbering_proposal(tracks, mode="stage", scope="all", include_hidden=False,
                                            start=20).changes, ((8, 20), (9, 21), (11, 22)))
        self.assertEqual([item.nr for item in tracks], [8, 2, 9, 11])

    def test_start_default_uses_preceding_row_not_largest_number(self):
        tracks = [track(8), track(2, 13), track(20), track(21)]
        self.assertEqual(numbering_proposal(tracks, (20, 21)).start, 3)
        self.assertEqual(numbering_proposal(tracks, (20, 21), mode="stage").start, 14)
        self.assertEqual(numbering_proposal(tracks, scope="all").start, 1)

    def test_renumber_blocks_conflicts_and_invalid_scope_without_mutation(self):
        tracks = [track(1, 7), track(2, 8), track(3, 8)]
        for options in (dict(start=2), dict(mode="stage", start=8), dict(start=0), dict(start="bad")):
            with self.subTest(options=options), self.assertRaises(ValueError):
                numbering_proposal(tracks, (1,), **options)
        self.assertEqual([item.nr for item in tracks], [1, 2, 3])
        with self.assertRaises(ValueError):
            numbering_proposal(tracks, ())

    def test_stage_renumber_assigns_one_per_track(self):
        tracks = [track(1, 7), track(2, 7), track(3, 7)]
        self.assertEqual(numbering_proposal(tracks, mode="stage", scope="all", start=10).changes,
                         ((1, 10), (2, 11), (3, 12)))

    def test_colors_types_hidden_and_setting_overrides(self):
        self.assertEqual(track_color(track(1)), TYPE_COLORS["unassigned"])
        self.assertEqual(track_color(track(1, kind="hiking"), {"track_types.hiking_color": "#112233"}), "#112233")
        self.assertEqual(track_color(track(1, kind="hiking", hidden=True)), "#AAAAAA")

    def test_all_vector_symbols_are_finite_and_have_distinct_artwork(self):
        shapes = []
        for kind in TYPE_LABELS:
            paths = symbol_paths(kind)
            self.assertTrue(paths)
            self.assertTrue(all(0 <= x <= 1 and 0 <= y <= 1 for path in paths for x, y in path))
            shapes.append(paths)
        self.assertEqual(len(set(shapes)), len(TYPE_LABELS))

    def test_metadata_undo_is_compact_and_preserves_cached_geometry(self):
        item = track(1)
        item.element.find(qname("type"))
        ET.SubElement(item.element, qname("type")).text = "unknown imported type"
        item.processed = object()
        geometry = item.processed
        controller = SimpleNamespace(tracks=[item], selected_nrs=[1],
            selected_tracks=lambda: [item], metadata_changed=Mock())
        controller.metadata_snapshot = lambda items, kind=MutationKind.METADATA: GPXEditorController.metadata_snapshot(controller, items, kind)
        entry = controller.metadata_snapshot([item])
        item.set_stage_number(10)
        item.set_track_type("bike")
        inverse = GPXEditorController.restore_metadata(controller, entry)
        self.assertEqual(item.element.findtext(qname("type")), "unknown imported type")
        self.assertEqual(item.stage_number, 1)
        self.assertIs(item.processed, geometry)
        GPXEditorController.restore_metadata(controller, inverse)
        self.assertEqual(item.track_type, "bike")
        self.assertEqual(item.stage_number, 10)

    def test_metadata_undo_survives_structural_snapshot_restoration(self):
        original = track(1, kind="horse")
        controller = SimpleNamespace(tracks=[original], selected_nrs=[1], metadata_changed=Mock())
        controller.selected_tracks = lambda: controller.tracks
        controller.metadata_snapshot = lambda items, kind=MutationKind.METADATA: GPXEditorController.metadata_snapshot(controller, items, kind)
        entry = controller.metadata_snapshot([original])
        original.set_track_type("bike")
        restored = TrackRecord(1, ET.fromstring(ET.tostring(original.element)))
        controller.tracks = [restored]
        GPXEditorController.restore_metadata(controller, entry)
        self.assertEqual(restored.track_type, "horse")
        self.assertEqual(original.track_type, "bike")

    def test_multiple_numbering_undos_after_structural_restore(self):
        original = track(1)
        controller = SimpleNamespace(tracks=[original], selected_nrs=[1], metadata_changed=Mock(),
                                     remap_track_number_state=Mock())
        controller.selected_tracks = lambda: controller.tracks
        controller.metadata_snapshot = lambda items, kind=MutationKind.METADATA: GPXEditorController.metadata_snapshot(controller, items, kind)
        first = controller.metadata_snapshot([original], MutationKind.ORDER)
        original.set_order_number(5)
        second = controller.metadata_snapshot([original], MutationKind.ORDER)
        original.set_order_number(10)
        replacement = TrackRecord(1, ET.fromstring(ET.tostring(original.element)))
        controller.tracks = [replacement]
        inverse_second = GPXEditorController.restore_metadata(controller, second)
        self.assertEqual(replacement.track_number, 5)
        inverse_first = GPXEditorController.restore_metadata(controller, first)
        self.assertEqual(replacement.track_number, 1)
        GPXEditorController.restore_metadata(controller, inverse_first)
        GPXEditorController.restore_metadata(controller, inverse_second)
        self.assertEqual(replacement.track_number, 10)
        self.assertEqual(replacement.internal_id, 1)

    def test_batch_assignment_scope_and_one_notification(self):
        tracks = [track(1), track(2, hidden=True), track(3)]
        controller = SimpleNamespace(tracks=tracks, selected_nrs=[1, 2],
            ensure_track_mutations_available=lambda: True,
            track_metadata_options=Mock(return_value=("bike", "selected", False, None)),
            push_metadata_undo=Mock(), metadata_changed=Mock())
        GPXEditorController.setTrackType_.callable(controller, None)
        self.assertEqual([item.track_type for item in tracks], ["bike", "unassigned", "unassigned"])
        controller.push_metadata_undo.assert_called_once_with([tracks[0]])
        controller.metadata_changed.assert_called_once()

    def test_select_by_type_replaces_selection_and_can_exclude_hidden(self):
        tracks = [track(1, kind="bike"), track(2, kind="bike", hidden=True), track(3)]
        controller = SimpleNamespace(tracks=tracks, selected_nrs=[3],
            track_metadata_options=Mock(return_value=("bike", "all", False, None)),
            update_selection_field=Mock(), highlight_selected_rows=Mock(), redraw_open_plot_views=Mock())
        GPXEditorController.selectTracksByType_.callable(controller, None)
        self.assertEqual(controller.selected_nrs, [1])

    def test_numbering_action_preserves_selection_geometry_and_single_transaction(self):
        tracks = [track(5), track(8)]
        for item in tracks:
            item.processed = object()
        geometry = [item.processed for item in tracks]
        controller = SimpleNamespace(tracks=tracks, selected_nrs=[8],
            selected_tracks=lambda: [tracks[1]], ensure_track_mutations_available=lambda: True,
            track_metadata_options=Mock(return_value=("track", "all", True, "20", "assign")),
            confirm_numbering_preview=Mock(return_value=True), push_metadata_undo=Mock(), metadata_changed=Mock(),
            remap_track_number_state=Mock())
        GPXEditorController.renumberTracks_.callable(controller, None)
        self.assertEqual([item.nr for item in tracks], [5, 8])
        self.assertEqual([item.track_number for item in tracks], [20, 21])
        self.assertEqual(controller.selected_nrs, [8])
        self.assertEqual([item.processed for item in tracks], geometry)
        controller.push_metadata_undo.assert_called_once()
        controller.metadata_changed.assert_called_once_with("Renumbered tracks.")
        self.assertIn("5 → 20", controller.confirm_numbering_preview.call_args.args[0])

    def test_number_state_preserves_retained_view_identities_without_touching_tiles(self):
        first, second, viewport = object(), object(), object()
        view = SimpleNamespace(plot_info=dict(tracks={1: first, 2: second}, current_track_nr=1),
                               schedule_interactive_tiles=Mock())
        workspace = SimpleNamespace(plot_view=view, focused_track_nr=1, selected_track_identity="1",
                                    track_viewports={1: viewport})
        tracks = [track(1), track(2)]
        controller = SimpleNamespace(tracks=tracks, selected_nrs=[1], plot_windows=[], adventure_workspace_delegate=workspace,
            ensure_track_mutations_available=lambda: True,
            track_metadata_options=Mock(return_value=("track", "all", True, "20", "assign")),
            confirm_numbering_preview=Mock(return_value=True), push_metadata_undo=Mock(), metadata_changed=Mock())
        GPXEditorController.renumberTracks_.callable(controller, None)
        self.assertIs(view.plot_info["tracks"][1], first)
        self.assertEqual(view.plot_info["current_track_nr"], 1)
        self.assertEqual(workspace.focused_track_nr, 1)
        self.assertEqual(workspace.selected_track_identity, "1")
        self.assertIs(workspace.track_viewports[1], viewport)
        view.schedule_interactive_tiles.assert_not_called()

    def test_conflicting_numbering_action_is_nonmutating(self):
        tracks = [track(1, 3), track(2, 4)]
        controller = SimpleNamespace(tracks=tracks, selected_nrs=[1],
            ensure_track_mutations_available=lambda: True,
            track_metadata_options=Mock(return_value=("stage", "selected", True, "4", "assign")),
            push_metadata_undo=Mock(), metadata_changed=Mock())
        with patch("GPXEditor.show_alert") as alert:
            GPXEditorController.renumberTracks_.callable(controller, None)
        alert.assert_called_once()
        controller.push_metadata_undo.assert_not_called()
        controller.metadata_changed.assert_not_called()
        self.assertEqual([item.stage_number for item in tracks], [3, 4])

    def test_pdf_subset_retains_complete_document_ids(self):
        tracks = [track(1, 3), track(2, 3), track(3, 3)]
        ids = track_ids(tracks)
        rows = tuple(summary_track(item, i, ids) for i, item in enumerate(tracks, 1))
        single = SummarySnapshot("Test", (rows[1],), SummaryOptions(columns=("stage", "name")))
        self.assertEqual(single.options.columns, ("stage", "name"))
        multiple = SummarySnapshot("Test", (rows[0], rows[2]), SummaryOptions(columns=("stage", "name")))
        self.assertEqual(multiple.options.columns, ("stage", "track_id", "name"))
        self.assertEqual([row.cells[:2] for row in summary_rows(multiple)[:2]], [("3", "3a"), ("3", "3c")])
        self.assertEqual(report_stage_title(tracks[1], [tracks[1]], ids), "Stage 3: Track 2")
        self.assertEqual(report_stage_title(tracks[2], [tracks[0], tracks[2]], ids), "Stage 3 · Track 3c: Track 3")

    def test_marker_sources_and_unassigned_fallback(self):
        from GPSTrackShow import time_lapse_marker_style
        for kind in TYPE_LABELS:
            self.assertNotEqual(time_lapse_marker_style("car", source="track_type", track_type=kind, overview=False), "")
        self.assertEqual(time_lapse_marker_style("car", source="track_type", track_type=None, overview=False), "arrow")
        self.assertEqual(time_lapse_marker_style("car", source="global", track_type="hiking", overview=False), "car")
        self.assertEqual(time_lapse_marker_style("car", source="track_type", track_type="hiking", overview=False), "hiking")

    def test_type_color_resolution_is_cached_and_changes_with_settings(self):
        from GPSTrackShow import type_route_color
        colors = tuple(TYPE_COLORS.items())
        type_route_color.cache_clear()
        first = type_route_color("hiking", colors)
        for _ in range(10):
            self.assertIs(type_route_color("hiking", colors), first)
        self.assertEqual(type_route_color.cache_info().misses, 1)
        self.assertNotEqual(type_route_color("hiking", tuple({**TYPE_COLORS, "hiking": "#000000"}.items())), first)

    def test_invalid_color_options_are_rejected_before_drawing(self):
        from GPSTrackShow import validated_type_colors
        with self.assertRaises(ValueError):
            validated_type_colors([])
        with self.assertRaises(ValueError):
            validated_type_colors({"hiking": "not a color"})
        self.assertEqual(dict(validated_type_colors({"hiking": "blue"}))["hiking"], (0., 0., 1., 1.))

    def test_profile_tint_reuses_retained_image_on_animation_ticks(self):
        from GPSTrackShow import ElevationProfileOverlayView
        source, background, metadata, colored = object(), object(), {}, object()
        view = SimpleNamespace(map_image=None, map_metadata=None, profile_image=None,
            profile_source_image=None, profile_color_key=None, profile_metadata=None,
            header_background_style="off", header_visible=False,
            setHidden_=Mock(), setNeedsDisplay_=Mock())
        with patch("GPSTrackShow.colorized_profile_image", return_value=colored) as tint:
            for index in range(12):
                ElevationProfileOverlayView.configure(view, background, metadata, source, metadata,
                    pilgrim_marker=(index, 100), route_color=(0., .5, 0., 1.))
            tint.assert_called_once()
            self.assertIs(view.profile_image, colored)
            ElevationProfileOverlayView.configure(view, background, metadata, source, metadata,
                route_color=(0., 0., .5, 1.))
            self.assertEqual(tint.call_count, 2)


class BackgroundOnlyMapTests(unittest.TestCase):
    def test_only_supported_backgrounds_are_usable(self):
        for payload in ({}, {"background_only": False, "map_content_version": MAP_CONTENT_VERSION},
                        {"background_only": True, "map_content_version": MAP_CONTENT_VERSION - 1},
                        {"background_only": True, "map_content_version": MAP_CONTENT_VERSION + 1}):
            self.assertFalse(map_uses_dynamic_overlays(payload))
        self.assertTrue(map_uses_dynamic_overlays({"background_only": True, "map_content_version": MAP_CONTENT_VERSION}))

    def test_manual_acceptance_cannot_bless_a_baked_image(self):
        from adventure_parameters import add_accepted_render_signature, map_render_signature_matches
        data = dict(background_only=False, map_content_version=MAP_CONTENT_VERSION,
                    adventure_render_parameters={"maps.output_provider": "osm"})
        original = copy.deepcopy(data)
        self.assertFalse(add_accepted_render_signature(data, {"maps.output_provider": "geoapify"}, accepted_at="now"))
        self.assertFalse(map_render_signature_matches(data, original["adventure_render_parameters"]))
        self.assertEqual(data, original)

    def test_pair_install_and_rollback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image, sidecar, staged = root / "map.png", root / "map.json", root / "pending.png"
            image.write_bytes(b"old image")
            sidecar.write_text('{"old":true}')
            staged.write_bytes(b"new image")
            replace = os.replace
            calls = 0

            def fail_install(source, destination):
                nonlocal calls
                if Path(destination) == sidecar and calls == 0:
                    calls += 1
                    raise OSError("test disk failure")
                return replace(source, destination)

            with patch("plot_metadata_utils.os.replace", side_effect=fail_install), self.assertRaises(OSError):
                write_plot_metadata({"_staged_background_image": str(staged), "new": True}, sidecar)
            self.assertEqual(image.read_bytes(), b"old image")
            self.assertEqual(sidecar.read_text(), '{"old":true}')
            self.assertFalse(staged.exists())
            staged.write_bytes(b"new image")
            write_plot_metadata({"_staged_background_image": str(staged), "new": True}, sidecar)
            self.assertEqual(image.read_bytes(), b"new image")
            self.assertEqual(json.loads(sidecar.read_text()), {"new": True})

    def test_baked_assets_cannot_be_reconciled_or_relinked(self):
        from track_asset_relink import reconcile_track_sidecars, relink_numbered_track_assets
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "0001_Track.png").write_bytes(b"baked image")
            payload = {"track_geometry_fingerprint": "geometry", "map_content_version": MAP_CONTENT_VERSION,
                       "background_only": False, "track_number": 1}
            sidecar = root / "0001_Track.json"
            sidecar.write_text(json.dumps(payload))
            context = dict(output_dir=root, args=SimpleNamespace(gpx_file=root / "test.gpx"),
                tracks=[dict(table_number=1, track_geometry_fingerprint="geometry", track_plot_image_filename="0001_Track.png")])
            self.assertEqual(reconcile_track_sidecars(context).unmatched, [sidecar.name])
            self.assertFalse(relink_numbered_track_assets(context).relinked)
            self.assertEqual(json.loads(sidecar.read_text()), payload)

    def test_presentation_refresh_does_not_write_png_or_change_fingerprints(self):
        from gpx_tracks_table import refresh_track_presentation_metadata
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "0001_Track.png"
            image.write_bytes(b"background")
            payload = {"track_geometry_fingerprint": "geometry", "map_content_version": MAP_CONTENT_VERSION,
                       "background_only": True, "track_number": 1}
            image.with_suffix(".json").write_text(json.dumps(payload))
            context = dict(output_dir=root, tracks=[dict(track_type="bike", stage_number=3, track_id="3a",
                internal_id=42, display_track_number=None, track_plot_image_filename=image.name)])
            signature = image.stat().st_mtime_ns
            refresh_track_presentation_metadata(context)
            updated = json.loads(image.with_suffix(".json").read_text())
            self.assertEqual(updated["track_type"], "bike")
            self.assertEqual(updated["internal_id"], 42)
            self.assertIsNone(updated["display_track_number"])
            self.assertEqual(updated["track_geometry_fingerprint"], "geometry")
            self.assertEqual(image.stat().st_mtime_ns, signature)

    def test_overview_presentation_refresh_preserves_segment_and_duplicate_order(self):
        from gpx_tracks_table import refresh_track_presentation_metadata
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sidecar = root / "overview.json"
            data = dict(background_only=True, map_content_version=MAP_CONTENT_VERSION,
                        source_track_geometry_fingerprints=["same", "same"],
                        tracks=[dict(track_geometry_fingerprint="same") for _ in range(2)])
            sidecar.write_text(json.dumps(data))
            context = dict(output_dir=root, overview_metadata_path=sidecar, tracks=[
                dict(track_geometry_fingerprint="same", track_type="bike", stage_number=3, track_id="3a", segment_count=2),
                dict(track_geometry_fingerprint="same", track_type="hiking", stage_number=3, track_id="3b", segment_count=1, hidden=True)])
            refresh_track_presentation_metadata(context)
            result = json.loads(sidecar.read_text())
            self.assertEqual(result["overlay_track_types"], ["bike", "bike", "hiking"])
            self.assertEqual(result["overlay_hidden_tracks"], [False, False, True])
            self.assertEqual([item["track_id"] for item in result["tracks"]], ["3a", "3b"])


if __name__ == "__main__":
    unittest.main()
