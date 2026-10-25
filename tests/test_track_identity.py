import copy
import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from GPXEditor import (GPXEditorController, TrackRecord, build_selected_tracks_root,
                       duplicate_track_records, get_or_create_mycamino_track_extension,
                       prepare_gpx_sources_for_load, qname)
from gpx_import import load_gpx_document
from gpx_processing import ProcessingOptions, processing_input_fingerprint, semantic_track_fingerprint
from gpx_pdf_summary import SummaryOptions, SummarySnapshot, summary_rows, summary_track
from track_types import (allocate_track_ids, apply_numbering_proposal, document_next_internal_id,
                         numbering_proposal, store_document_counter, stored_internal_id, track_ids)


def record(identity, number=None, stage=7, hidden=False):
    element = ET.fromstring('<trk xmlns="http://www.topografix.com/GPX/1/1">'
        '<name>Route</name><trkseg><trkpt lat="50" lon="7"><ele>123</ele>'
        '<time>2026-07-01T10:00:00Z</time></trkpt></trkseg></trk>')
    item = TrackRecord(identity, element, metrics={"length_km": 1, "npoints": 1}, metrics_dirty=False)
    item.set_order_number(number)
    item.set_stage_number(stage)
    if hidden:
        item.set_hidden(True)
    return item


def controller_for(tracks, selected=(), result=None):
    return SimpleNamespace(tracks=tracks, selected_nrs=list(selected),
        ensure_track_mutations_available=lambda: True,
        track_metadata_options=Mock(return_value=result),
        confirm_numbering_preview=Mock(return_value=True), push_metadata_undo=Mock(),
        metadata_changed=Mock(), set_status=Mock())


class StableTrackIdentityTests(unittest.TestCase):
    def test_internal_id_is_immutable_and_number_is_independent(self):
        item = record(42, 3)
        self.assertEqual(item.internal_id, 42)
        with self.assertRaises(AttributeError):
            item.nr = 3
        item.set_order_number(9)
        self.assertEqual(item.internal_id, 42)
        self.assertEqual(stored_internal_id(item.element), 42)
        self.assertEqual(item.track_number, 9)
        self.assertEqual(copy.copy(item).internal_id, 42)
        with self.assertRaises(ValueError):
            record(0)

    def test_allocator_reserves_future_ids_and_rejects_duplicates(self):
        first, second, third = (record(8, 1), record(8, 2), record(2, 3))
        ids, counter = allocate_track_ids([item.element for item in (first, second, third)], 20)
        self.assertEqual(ids, [8, 20, 2])
        self.assertEqual(counter, 21)
        fresh, counter = allocate_track_ids([first.element, second.element], counter,
                                           preserve=False, used=ids)
        self.assertEqual(fresh, [21, 22])
        self.assertEqual(counter, 23)

    def test_legacy_adoption_preserves_numbers_stages_and_cleared_values(self):
        elements = []
        for number in (8, 3):
            element = ET.fromstring('<trk xmlns="http://www.topografix.com/GPX/1/1"><trkseg/></trk>')
            get_or_create_mycamino_track_extension(element).set("order_number", str(number))
            elements.append(element)
        get_or_create_mycamino_track_extension(elements[1]).set("track_number_unassigned", "yes")
        ids, counter = allocate_track_ids(elements)
        tracks = [TrackRecord(identity, element) for identity, element in zip(ids, elements)]
        self.assertEqual(ids, [8, 3])
        self.assertEqual(counter, 9)
        self.assertEqual([item.track_number for item in tracks], [8, None])
        self.assertEqual([item.stage_number for item in tracks], [8, 3])
        self.assertEqual(allocate_track_ids([item.element for item in tracks], counter)[0], ids)

    def test_numbering_and_identity_metadata_do_not_invalidate_calculations(self):
        item = record(50, 2)
        fingerprint = semantic_track_fingerprint(item.element)
        processing = processing_input_fingerprint(item.element)
        for mode, action in (("both", "clear"), ("track", "assign"), ("stage", "assign")):
            proposal = numbering_proposal([item], [50], mode=mode, action=action, start=8)
            apply_numbering_proposal([item], proposal)
            self.assertEqual(semantic_track_fingerprint(item.element), fingerprint)
            self.assertEqual(processing_input_fingerprint(item.element), processing)
        self.assertEqual(item.internal_id, 50)

    def test_cleared_number_is_available_to_a_different_internal_id(self):
        tracks = [record(1, 1), record(99, 2)]
        apply_numbering_proposal(tracks, numbering_proposal(tracks, [1], action="clear"))
        proposal = numbering_proposal(tracks, [99], start=1)
        apply_numbering_proposal(tracks, proposal)
        self.assertEqual([item.track_number for item in tracks], [None, 1])
        self.assertEqual([item.internal_id for item in tracks], [1, 99])
        with self.assertRaises(ValueError):
            numbering_proposal(tracks, [1], start=1)

    def test_clear_both_uses_same_preview_scope_and_single_transaction(self):
        tracks = [record(10, 3), record(20, 4, hidden=True), record(30, 5)]
        controller = controller_for(tracks, [10, 20], ("both", "selected", False, "invalid", "clear"))
        GPXEditorController.renumberTracks_.callable(controller, None)
        self.assertEqual([item.track_number for item in tracks], [None, 4, 5])
        self.assertEqual([item.stage_number for item in tracks], [None, 7, 7])
        controller.push_metadata_undo.assert_called_once_with([tracks[0]])
        controller.metadata_changed.assert_called_once_with("Cleared numbers.")
        preview = controller.confirm_numbering_preview.call_args.args[0]
        self.assertIn("Track: 3", preview)
        self.assertIn("Stage: 7", preview)
        self.assertIn("Unassigned", preview)
        self.assertNotIn("10", preview)

    def test_cancel_and_noop_never_push_undo_or_mark_dirty(self):
        item = record(45, 1)
        controller = controller_for([item], [45], ("track", "all", True, "1", "assign"))
        GPXEditorController.renumberTracks_.callable(controller, None)
        controller.confirm_numbering_preview.assert_not_called()
        controller.push_metadata_undo.assert_not_called()
        controller.metadata_changed.assert_not_called()
        controller.track_metadata_options.return_value = ("track", "all", True, "2", "assign")
        controller.confirm_numbering_preview.return_value = False
        GPXEditorController.renumberTracks_.callable(controller, None)
        self.assertEqual(item.track_number, 1)
        controller.push_metadata_undo.assert_not_called()
        controller.metadata_changed.assert_not_called()
        controller.track_metadata_options.return_value = None
        GPXEditorController.renumberTracks_.callable(controller, None)

    def test_suffixes_follow_visible_numbers_then_unnumbered_ids_including_hidden(self):
        tracks = [record(40, None), record(9, 3), record(80, 1, hidden=True), record(5, None)]
        self.assertEqual(track_ids(tracks), {80: "7a", 9: "7b", 5: "7c", 40: "7d"})
        tracks[0].set_order_number(2)
        self.assertEqual(track_ids(list(reversed(tracks))), {80: "7a", 40: "7b", 9: "7c", 5: "7d"})

    def test_selection_field_translates_visible_numbers_not_ids(self):
        tracks = [record(50, 3), record(51, 1), record(2, None)]
        field = Mock()
        field.stringValue.return_value = "3,1"
        controller = SimpleNamespace(tracks=tracks, selected_nrs=[], selection_field=field,
            highlight_selected_rows=Mock(), redraw_open_plot_views=Mock())
        controller.update_selection_field = lambda: GPXEditorController.update_selection_field(controller)
        GPXEditorController.selectionFieldCommitted_.callable(controller, None)
        self.assertEqual(controller.selected_nrs, [50, 51])
        controller.selected_nrs.append(2)
        controller.update_selection_field()
        self.assertIn("1 unnumbered", field.setToolTip_.call_args.args[0])
        self.assertNotIn("50", field.setStringValue_.call_args.args[0])

    def test_restore_snapshot_does_not_roll_back_next_id(self):
        item = record(8, 1)
        controller = SimpleNamespace(tracks=[item], next_nr=20, dirty=False, recalculate=Mock())
        snapshot = GPXEditorController.snapshot(controller)
        controller.tracks = []
        controller.next_nr = 27
        GPXEditorController.restore_snapshot(controller, snapshot)
        self.assertEqual(controller.next_nr, 27)
        self.assertEqual(controller.tracks[0].internal_id, 8)

    def test_save_and_recovery_remain_available_after_deleting_all_tracks(self):
        controller = SimpleNamespace(tracks=[], document_ids_initialized=True, dirty=True,
            output_field=Mock(), output_path_from_field=Mock(return_value=Path("/tmp/saved.gpx")),
            save_to_path=Mock())
        controller.output_field.stringValue.return_value = "/tmp/saved.gpx"
        self.assertTrue(GPXEditorController.save_current(controller))
        controller.save_to_path.assert_called_once_with(Path("/tmp/saved.gpx"))
        controller.save_to_path.reset_mock()
        GPXEditorController.autosave_.callable(controller, None)
        self.assertTrue(controller.save_to_path.call_args.kwargs["autosave"])

    def test_saved_counter_survives_highest_id_deletion_and_empty_document(self):
        controller = SimpleNamespace(tracks=[record(1, 9)], next_nr=101, project_name="Test", anchor=None)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "saved.gpx"
            root = GPXEditorController.build_root(controller)
            self.assertEqual(list(root)[-1].tag, qname("extensions"))
            ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
            document = load_gpx_document(path)
            self.assertEqual(document.next_internal_id, 101)
            self.assertEqual(allocate_track_ids(document.tracks, document.next_internal_id)[0], [1])
            controller.tracks = []
            root = GPXEditorController.build_root(controller)
            ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
            document = load_gpx_document(path)
            self.assertEqual(document.tracks, [])
            self.assertEqual(document.next_internal_id, 101)
            item = record(1, 1)
            get_or_create_mycamino_track_extension(item.element).attrib.pop("internal_id")
            ids, _counter = allocate_track_ids([item.element], document.next_internal_id, legacy=False)
            self.assertEqual(ids, [101])

    def test_append_reopens_ids_but_import_assigns_fresh_ids_and_keeps_counter(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.gpx"
            source = SimpleNamespace(tracks=[record(8, 2)], next_nr=40, project_name="Test", anchor=None)
            ET.ElementTree(GPXEditorController.build_root(source)).write(path, encoding="utf-8", xml_declaration=True)
            prepared, errors = prepare_gpx_sources_for_load([path], ProcessingOptions())
            self.assertEqual(errors, [])
            controller = SimpleNamespace(tracks=[], next_nr=1, project_name="Test", document_ids_initialized=False)
            GPXEditorController._append_gpx_track_records(controller, [path], prepared)
            self.assertEqual([item.internal_id for item in controller.tracks], [8])
            self.assertEqual(controller.next_nr, 40)
            prepared, _errors = prepare_gpx_sources_for_load([path], ProcessingOptions())
            GPXEditorController._append_gpx_track_records(controller, [path], prepared)
            self.assertEqual([item.internal_id for item in controller.tracks], [8, 40])
            self.assertEqual([item.track_number for item in controller.tracks], [2, 3])
            controller.tracks.clear()
            prepared, _errors = prepare_gpx_sources_for_load([path], ProcessingOptions())
            GPXEditorController._append_gpx_track_records(controller, [path], prepared)
            self.assertEqual(controller.tracks[0].internal_id, 41)

    def test_duplicates_have_fresh_ids_and_join_retains_survivor_identity(self):
        first, second = record(8, 1), record(9, 2)
        _tracks, duplicates, next_id = duplicate_track_records([first, second], [0], 40)
        self.assertEqual(duplicates[0].internal_id, 40)
        self.assertEqual(stored_internal_id(duplicates[0].element), 40)
        self.assertEqual(duplicates[0].track_number, 3)
        self.assertEqual(next_id, 41)
        GPXEditorController.apply_track_metadata(SimpleNamespace(), first, second)
        self.assertEqual(stored_internal_id(first.element), 8)
        self.assertEqual(first.track_number, 1)

    def test_split_and_cut_allocate_new_ids_without_reusing_deleted_ids(self):
        for operation in ("cut", "split"):
            with self.subTest(operation=operation):
                item = record(8, 2)
                segment = item.element.find(qname("trkseg"))
                for index in range(1, 5):
                    ET.SubElement(segment, qname("trkpt"), lat=str(50 + index * .001), lon="7")
                controller = SimpleNamespace(tracks=[item], next_nr=100, push_undo=Mock(),
                    invalidate_track_metrics=Mock(), mark_dirty=Mock(), update_selection_field=Mock(),
                    unique_split_track_name=lambda name: name + " (2)")
                if operation == "cut":
                    GPXEditorController.cut_track(controller, item, 2, refresh=False)
                    added = controller.tracks[1]
                else:
                    added = GPXEditorController.split_track_from_index(controller, item, 2)
                self.assertEqual(item.internal_id, 8)
                self.assertEqual(stored_internal_id(item.element), 8)
                self.assertEqual(added.internal_id, 100)
                self.assertEqual(stored_internal_id(added.element), 100)
                self.assertEqual(added.track_number, 3)
                self.assertEqual(controller.next_nr, 101)

    def test_all_clear_modes_hidden_scope_and_default_start(self):
        for mode in ("track", "stage", "both"):
            with self.subTest(mode=mode):
                tracks = [record(41, 4, stage=2), record(42, 5, stage=3, hidden=True)]
                proposal = numbering_proposal(tracks, scope="all", mode=mode, action="clear", include_hidden=False)
                self.assertEqual(proposal.changes, ((41, None),))
                apply_numbering_proposal(tracks, proposal)
                self.assertEqual(tracks[0].track_number, None if mode in {"track", "both"} else 4)
                self.assertEqual(tracks[0].stage_number, None if mode in {"stage", "both"} else 2)
                self.assertEqual((tracks[1].track_number, tracks[1].stage_number), (5, 3))
        tracks = [record(41, None), record(42, 6), record(99, None)]
        self.assertEqual(numbering_proposal(tracks, [99]).start, 7)

    def test_pdf_numbers_and_export_namespace_never_expose_internal_ids(self):
        tracks = [record(101, 2), record(104, None)]
        snapshot = SummarySnapshot("Test", tuple(summary_track(item, i, track_ids(tracks))
            for i, item in enumerate(tracks, 1)), SummaryOptions(columns=("nr", "name")))
        self.assertEqual([row.cells[0] for row in summary_rows(snapshot)[:2]], ["2", ""])
        root = build_selected_tracks_root(tracks, Path("/tmp/selected.gpx"))
        self.assertEqual([stored_internal_id(element) for element in root.findall(qname("trk"))], [1, 2])
        self.assertEqual(document_next_internal_id(root), 3)
        self.assertEqual([item.internal_id for item in tracks], [101, 104])

    def test_numbering_dialog_disables_start_and_both_when_appropriate(self):
        operation, choice, start = Mock(), Mock(), Mock()
        controller = SimpleNamespace(numbering_dialog_controls=(operation, choice, start))
        operation.indexOfSelectedItem.return_value = 1
        GPXEditorController.numberingOptionsChanged_.callable(controller, None)
        start.setEnabled_.assert_called_with(False)
        choice.itemAtIndex_.return_value.setEnabled_.assert_called_with(True)
        operation.indexOfSelectedItem.return_value = 0
        choice.indexOfSelectedItem.return_value = 2
        GPXEditorController.numberingOptionsChanged_.callable(controller, None)
        start.setEnabled_.assert_called_with(True)
        choice.itemAtIndex_.return_value.setEnabled_.assert_called_with(False)
        choice.selectItemAtIndex_.assert_called_with(0)

    def test_numbering_preserves_asset_paths_fingerprints_and_control_references(self):
        from gpx_tracks_table import prepare_with_options
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trip.gpx"
            control = Path(directory) / "trip.lst"
            control.write_text("#Map: 0001_Route_trip.png\n", encoding="utf-8")
            tracks = [record(10, 1), record(20, 2)]
            controller = SimpleNamespace(tracks=tracks, next_nr=100, project_name="Trip", anchor=None)
            def prepare():
                ET.ElementTree(GPXEditorController.build_root(controller)).write(path, encoding="utf-8", xml_declaration=True)
                return prepare_with_options(str(path), output_dir=str(Path(directory) / "maps"), sort_original=True)
            with patch("urllib.request.urlopen", side_effect=AssertionError("No network allowed")), \
                    patch("requests.sessions.Session.request", side_effect=AssertionError("No network allowed")):
                before = prepare()
                apply_numbering_proposal(tracks, numbering_proposal(tracks, scope="all", start=21))
                after = prepare()
            for first, second in zip(before["tracks"], after["tracks"]):
                for key in ("internal_id", "track_geometry_fingerprint", "track_data_fingerprint",
                            "track_plot_image_filename", "track_plot_time_lapse_image_filename", "track_data_sidecar"):
                    self.assertEqual(first[key], second[key], key)
            self.assertEqual([item["display_track_number"] for item in after["tracks"]], [21, 22])
            self.assertEqual(control.read_text(encoding="utf-8"), "#Map: 0001_Route_trip.png\n")

    def test_asset_identity_disambiguates_same_geometry_without_guessing(self):
        from map_overlay import MAP_CONTENT_VERSION
        from track_asset_relink import reconcile_track_sidecars
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = dict(background_only=True, map_content_version=MAP_CONTENT_VERSION,
                track_geometry_fingerprint="same", internal_id=22)
            for name, data in (("0001_Known", payload), ("0002_Unknown", {**payload, "internal_id": None})):
                (root / f"{name}.png").write_bytes(b"background")
                (root / f"{name}.json").write_text(json.dumps(data), encoding="utf-8")
            context = dict(output_dir=root, args=SimpleNamespace(gpx_file=root / "trip.gpx"), tracks=[
                dict(table_number=1, internal_id=11, name="First", track_geometry_fingerprint="same"),
                dict(table_number=2, internal_id=22, name="Second", track_geometry_fingerprint="same")])
            report = reconcile_track_sidecars(context)
            self.assertEqual(report.ambiguous, ["0002_Unknown.json"])
            known = json.loads((root / "0001_Known.json").read_text(encoding="utf-8"))
            self.assertEqual(known["track_name"], "Second")
            self.assertEqual(known["internal_id"], 22)
            self.assertEqual((root / "0001_Known.png").read_bytes(), b"background")


if __name__ == "__main__":
    unittest.main()
