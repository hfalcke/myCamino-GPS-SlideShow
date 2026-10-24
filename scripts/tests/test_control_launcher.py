from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


class ControlLauncherTests(unittest.TestCase):
    def test_plain_control_launcher_restarts_to_load_current_code(self):
        source = (ROOT / "control.sh").read_text(encoding="utf-8")
        self.assertIn('ops_control.launcher "${1:-restart}"', source)

    def test_restart_refuses_to_interrupt_an_operation(self):
        source = (ROOT / "ops_control" / "launcher.py").read_text(encoding="utf-8")
        self.assertIn("fcntl.LOCK_EX | fcntl.LOCK_NB", source)
        self.assertIn("An operation is running", source)


if __name__ == "__main__":
    unittest.main()
