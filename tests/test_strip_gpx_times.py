import contextlib
import io
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from xml.parsers.expat import ExpatError

from scripts.strip_gpx_times import main, strip_gpx_times


REPO = Path(__file__).resolve().parents[1]
DEMO = (REPO / "demo/St-Jean-Pied-de-Port-to-Roncesvalles/1 - GPX Track"
        / "St-Jean-Pied-de-Port-to-Roncesvalles.gpx")


def without_times(element):
    """Compare XML content, ignoring formatting and time elements only."""
    return (
        element.tag, element.attrib, (element.text or "").strip(),
        [without_times(child) for child in element
         if child.tag.rsplit("}", 1)[-1] != "time"],
    )


class StripGPXTimesTests(unittest.TestCase):
    def test_demo_preserves_everything_except_times(self):
        original = DEMO.read_bytes()
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "untimed.gpx"
            count = strip_gpx_times(DEMO, output)
            before = ET.fromstring(original)
            after = ET.parse(output).getroot()
            self.assertEqual(count, 2409)  # 2408 points plus one Guru Maps time.
            self.assertFalse(any(node.tag.rsplit("}", 1)[-1] == "time"
                                 for node in after.iter()))
            self.assertEqual(without_times(before), without_times(after))
            self.assertEqual(len(after.findall(".//{*}trkpt")), 2408)
        self.assertEqual(DEMO.read_bytes(), original)

    def test_all_time_locations_and_cdata(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "input.gpx"
            output = Path(folder) / "output.gpx"
            source.write_text('''<gpx xmlns="http://www.topografix.com/GPX/1/1"
                xmlns:vendor="urn:vendor"><metadata><time>2024-01-01Z</time></metadata>
                <wpt lat="1" lon="2"><time>2024-01-01Z</time>
                <desc><![CDATA[A < B & C]]></desc></wpt>
                <rte><rtept lat="1" lon="2"><time>2024-01-01Z</time></rtept></rte>
                <trk><trkseg><trkpt lat="1" lon="2"><ele>123</ele>
                <time>2024-01-01Z</time><extensions><vendor:time>2024-01-01Z</vendor:time>
                <vendor:speed>1.2</vendor:speed></extensions></trkpt></trkseg></trk></gpx>''')
            self.assertEqual(strip_gpx_times(source, output), 5)
            self.assertIn("<![CDATA[A < B & C]]>", output.read_text())
            self.assertIn("<vendor:speed>1.2</vendor:speed>", output.read_text())
            self.assertFalse(any(line.rstrip() != line for line in output.read_text().splitlines()))
            self.assertNotIn("\n                \n", output.read_text())

    def test_existing_output_and_input_are_protected(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "input.gpx"
            source.write_text("<gpx/>")
            output = Path(folder) / "existing.gpx"
            output.write_text("keep me")
            with self.assertRaises(ValueError):
                strip_gpx_times(source, source)
            with self.assertRaises(FileExistsError):
                strip_gpx_times(source, output)
            self.assertEqual(source.read_text(), "<gpx/>")
            self.assertEqual(output.read_text(), "keep me")

    def test_invalid_input_creates_no_output(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "input.gpx"
            output = Path(folder) / "output.gpx"
            for content in ("<not-gpx/>", "<gpx>"):
                source.write_text(content)
                with self.assertRaises((ValueError, ExpatError)):
                    strip_gpx_times(source, output)
                self.assertFalse(output.exists())

    def test_default_output_and_already_untimed_input(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "input.gpx"
            source.write_text('<gpx><trk><trkseg><trkpt lat="1" lon="2"/></trkseg></trk></gpx>')
            with contextlib.redirect_stdout(io.StringIO()) as log:
                self.assertEqual(main([str(source)]), 0)
            self.assertTrue((Path(folder) / "input-untimed.gpx").is_file())
            self.assertIn("Removed 0 time elements", log.getvalue())


if __name__ == "__main__":
    unittest.main()
