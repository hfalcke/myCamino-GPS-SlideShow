"""Tests for hidden rows and visual caption/font directives."""

from __future__ import annotations

import unittest

from GPSTrackShow import CaptionFontState, build_caption_plan
from slideshow_control_format import (
    CaptionSyntaxError,
    FontSyntaxError,
    disable_control_line,
    enable_control_line,
    parse_caption_directive,
    parse_caption_parameters,
    parse_font_parameters,
    serialize_caption_parameters,
    serialize_font_parameters,
)


class SlideshowCaptionTests(unittest.TestCase):
    def test_caption_defaults_and_multiline_round_trip(self):
        simple = parse_caption_parameters("My caption")
        self.assertEqual((simple.vertical, simple.horizontal), ("bottom", "center"))
        directive = parse_caption_parameters('#TOP, #LEFT, "First, line\\nSecond"')
        self.assertEqual(directive.text, "First, line\nSecond")
        self.assertEqual(
            serialize_caption_parameters(directive),
            '#TOP, #LEFT, "First, line\\nSecond"',
        )

    def test_caption_rejects_unknown_commands_and_unquoted_comma_text(self):
        with self.assertRaises(CaptionSyntaxError):
            parse_caption_parameters("#SIDE, Caption")
        with self.assertRaises(CaptionSyntaxError):
            parse_caption_parameters("First, second")

    def test_partial_font_changes_and_default(self):
        directive = parse_font_parameters("#SIZE 36, #STYLE italic")
        self.assertEqual((directive.size, directive.style, directive.family), (36.0, "italic", None))
        self.assertEqual(serialize_font_parameters(directive), "#SIZE 36, #STYLE ITALIC")
        self.assertTrue(parse_font_parameters("#DEFAULT").reset)
        with self.assertRaises(FontSyntaxError):
            parse_font_parameters("#STYLE outline")

    def test_disabled_prefix_is_lossless_and_not_parsed_as_caption(self):
        line = "photo.jpeg | 12:00 | kein GPS | kein Ort"
        self.assertEqual(enable_control_line(disable_control_line(line)), line)
        self.assertIsNone(parse_caption_directive("# #CAPTION: Hidden"))

    def test_caption_targets_only_immediate_enabled_media(self):
        default = CaptionFontState(30.0, "bold", "System")
        lines = [
            "#FONT: #SIZE 40",
            "#CAPTION: #TOP, Caption",
            "photo.jpeg | 12:00 | kein GPS | kein Ort",
            "#CAPTION: Expired",
            "#Datum: Monday",
            "other.jpeg | 13:00 | kein GPS | kein Ort",
            "#CAPTION: Hidden target",
            "# hidden.jpeg | 14:00 | kein GPS | kein Ort",
        ]
        captions, fonts = build_caption_plan(lines, default)
        self.assertEqual(set(captions), {2})
        self.assertEqual(captions[2][0].text, "Caption")
        self.assertEqual(captions[2][1].size, 40.0)
        self.assertEqual(fonts[5].size, 40.0)


if __name__ == "__main__":
    unittest.main()
