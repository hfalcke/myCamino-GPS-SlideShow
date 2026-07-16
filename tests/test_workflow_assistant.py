import unittest

from workflow_assistant import (
    assistant_bubble_size,
    assistant_stage_uses_text_input,
    bubble_geometry,
    detected_gpx_choices,
    next_assistant_stage,
    normalize_assistant_state,
    relocated_bubble_geometry,
)


def complete_readiness():
    return {
        "project": True,
        "adventure": True,
        "gpx": True,
        "media": True,
        "metadata": True,
        "track_maps": True,
        "control": True,
    }


class WorkflowAssistantTests(unittest.TestCase):
    def test_new_and_existing_adventure_defaults_differ_only_for_action_markers(self):
        new_state = normalize_assistant_state(None, existing_adventure=False)
        old_state = normalize_assistant_state(None, existing_adventure=True)

        self.assertFalse(new_state["journey_source_confirmed"])
        self.assertFalse(new_state["media_confirmed"])
        self.assertFalse(new_state["metadata_prepared"])
        self.assertTrue(new_state["place_names_requested"])
        self.assertFalse(new_state["place_names_completed"])
        self.assertFalse(new_state["slideshow_started"])
        self.assertTrue(old_state["enabled"])
        self.assertTrue(old_state["journey_source_confirmed"])
        self.assertTrue(old_state["media_confirmed"])
        self.assertTrue(old_state["metadata_prepared"])
        self.assertTrue(old_state["place_names_completed"])
        self.assertTrue(old_state["slideshow_started"])

    def test_first_incomplete_data_stage_precedes_action_markers(self):
        readiness = complete_readiness()
        readiness["gpx"] = False
        state = normalize_assistant_state(None, existing_adventure=False)
        self.assertEqual(next_assistant_stage(readiness, state), "gpx")

    def test_visible_place_name_option_does_not_add_an_assistant_stage(self):
        readiness = complete_readiness()
        state = normalize_assistant_state(None, existing_adventure=False)
        state["journey_source_confirmed"] = True
        state["media_confirmed"] = True
        state["metadata_prepared"] = True
        self.assertEqual(next_assistant_stage(readiness, state), "slideshow")
        state["slideshow_started"] = True
        self.assertIsNone(next_assistant_stage(readiness, state))

    def test_disabled_assistant_has_no_stage(self):
        state = normalize_assistant_state(None, existing_adventure=False)
        state["enabled"] = False
        self.assertIsNone(next_assistant_stage({}, state))

    def test_only_assistant_input_stages_take_keyboard_focus(self):
        self.assertTrue(assistant_stage_uses_text_input("project"))
        self.assertTrue(assistant_stage_uses_text_input("adventure"))
        self.assertTrue(assistant_stage_uses_text_input("gpx"))
        self.assertFalse(assistant_stage_uses_text_input("media"))
        self.assertFalse(assistant_stage_uses_text_input("track_maps"))

    def test_bubble_prefers_above_target_and_stays_inside_container(self):
        geometry = bubble_geometry((0, 0, 800, 600), (300, 200, 120, 28))
        x, y, width, height = geometry.frame
        self.assertEqual(geometry.pointer_side, "bottom")
        self.assertGreater(y, 228)
        self.assertGreaterEqual(x, 10)
        self.assertLessEqual(x + width, 790)
        self.assertGreaterEqual(y, 10)
        self.assertLessEqual(y + height, 590)

    def test_bubble_uses_below_for_target_near_top_edge(self):
        geometry = bubble_geometry((0, 0, 500, 300), (180, 270, 100, 24))
        self.assertEqual(geometry.pointer_side, "top")
        self.assertLess(geometry.frame[1] + geometry.frame[3], 270)

    def test_bubble_size_grows_for_wrapped_text_and_controls(self):
        compact = assistant_bubble_size({"message": "Choose a project directory."})
        detailed = assistant_bubble_size(
            {
                "message": "Choose the detected GPX files, join several tracks in the editor, or continue without a GPX file.",
                "choices": (("one", "One"), ("two", "Two")),
                "actions": (("apply", "Apply"), ("cancel", "Cancel")),
            }
        )
        self.assertGreater(detailed[0], compact[0])
        self.assertGreater(detailed[1], compact[1])

    def test_relocated_bubble_keeps_origin_and_points_toward_target(self):
        geometry = relocated_bubble_geometry(
            (0, 0, 800, 600),
            (620, 430, 100, 30),
            (330, 120),
            (120, 90),
        )
        self.assertEqual(geometry.frame[:2], (120.0, 90.0))
        self.assertIn(geometry.pointer_side, {"top", "right"})

    def test_relocated_bubble_is_clamped_after_window_shrinks(self):
        geometry = relocated_bubble_geometry(
            (0, 0, 420, 260),
            (20, 20, 80, 24),
            (350, 150),
            (700, 500),
        )
        x, y, width, height = geometry.frame
        self.assertGreaterEqual(x, 10)
        self.assertGreaterEqual(y, 10)
        self.assertLessEqual(x + width, 410)
        self.assertLessEqual(y + height, 250)

    def test_detected_gpx_choices_cover_zero_one_and_many_files(self):
        self.assertEqual(
            detected_gpx_choices([]),
            (
                ("choose_other_gpx", "choose GPX files..."),
                ("no_gpx", "no GPX file - use only photos"),
            ),
        )
        self.assertEqual(
            detected_gpx_choices(["/tmp/day.gpx"]),
            (
                ("use_detected_gpx", "use day.gpx"),
                ("choose_other_gpx", "choose other GPX files..."),
                ("no_gpx", "no GPX file - use only photos"),
            ),
        )
        self.assertEqual(
            detected_gpx_choices(["/tmp/a.gpx", "/tmp/b.gpx"]),
            (
                ("join_detected_gpx", "join the 2 detected GPX files"),
                ("choose_other_gpx", "choose other GPX files..."),
                ("no_gpx", "no GPX file - use only photos"),
            ),
        )

if __name__ == "__main__":
    unittest.main()
