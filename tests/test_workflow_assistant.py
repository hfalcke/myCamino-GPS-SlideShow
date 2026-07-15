import unittest

from workflow_assistant import bubble_geometry, next_assistant_stage, normalize_assistant_state


def complete_readiness():
    return {
        "project": True,
        "adventure": True,
        "gpx": True,
        "track_maps": True,
        "media": True,
        "control": True,
    }


class WorkflowAssistantTests(unittest.TestCase):
    def test_new_and_existing_adventure_defaults_differ_only_for_action_markers(self):
        new_state = normalize_assistant_state(None, existing_adventure=False)
        old_state = normalize_assistant_state(None, existing_adventure=True)

        self.assertEqual(
            new_state,
            {"enabled": True, "place_names_completed": False, "slideshow_started": False},
        )
        self.assertTrue(old_state["enabled"])
        self.assertTrue(old_state["place_names_completed"])
        self.assertTrue(old_state["slideshow_started"])

    def test_first_incomplete_data_stage_precedes_action_markers(self):
        readiness = complete_readiness()
        readiness["gpx"] = False
        state = normalize_assistant_state(None, existing_adventure=False)
        self.assertEqual(next_assistant_stage(readiness, state), "gpx")

    def test_place_names_then_slideshow_complete_workflow(self):
        readiness = complete_readiness()
        state = normalize_assistant_state(None, existing_adventure=False)
        self.assertEqual(next_assistant_stage(readiness, state), "place_names")
        state["place_names_completed"] = True
        self.assertEqual(next_assistant_stage(readiness, state), "slideshow")
        state["slideshow_started"] = True
        self.assertIsNone(next_assistant_stage(readiness, state))

    def test_disabled_assistant_has_no_stage(self):
        state = normalize_assistant_state(None, existing_adventure=False)
        state["enabled"] = False
        self.assertIsNone(next_assistant_stage({}, state))

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


if __name__ == "__main__":
    unittest.main()
