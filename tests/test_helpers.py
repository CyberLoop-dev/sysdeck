import io
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import sysdeck  # noqa: E402


class HelperTests(unittest.TestCase):
    def test_human_bytes(self):
        self.assertEqual(sysdeck.human_bytes(0), "0 B")
        self.assertEqual(sysdeck.human_bytes(1023), "1023 B")
        self.assertEqual(sysdeck.human_bytes(1024), "1.0 KB")
        self.assertEqual(sysdeck.human_bytes(5 * 1024**3), "5.0 GB")
        self.assertEqual(sysdeck.human_bytes(3 * 1024**4), "3.0 TB")

    def test_human_rate(self):
        self.assertEqual(sysdeck.human_rate(2048), "2.0 KB/s")

    def test_format_uptime(self):
        self.assertEqual(sysdeck.format_uptime(5 * 60), "5m")
        self.assertEqual(sysdeck.format_uptime(3 * 3600 + 14 * 60), "3h 14m")
        self.assertEqual(sysdeck.format_uptime(2 * 86400 + 3 * 3600 + 14 * 60), "2d 03h 14m")

    def test_rate_never_negative(self):
        self.assertEqual(sysdeck.rate(100, 300, 2), 100)
        self.assertEqual(sysdeck.rate(300, 100, 2), 0.0)  # counter reset
        self.assertEqual(sysdeck.rate(0, 100, 0), 0.0)

    def test_sparkline(self):
        self.assertEqual(sysdeck.sparkline([], 5), "     ")
        line = sysdeck.sparkline([0, 50, 100], 3, 100)
        self.assertEqual(len(line), 3)
        self.assertEqual(line[0], "▁")
        self.assertEqual(line[-1], "█")
        self.assertEqual(len(sysdeck.sparkline(range(100), 10)), 10)

    def test_level_color(self):
        theme = sysdeck.THEMES["cyber"]
        self.assertEqual(sysdeck.level_color(10, theme), theme["main"])
        self.assertEqual(sysdeck.level_color(75, theme), theme["warn"])
        self.assertEqual(sysdeck.level_color(95, theme), theme["bad"])

    def test_bar_width_and_clamping(self):
        theme = sysdeck.THEMES["matrix"]
        text = sysdeck.bar(150, 10, theme)
        self.assertIn("100.0%", text.plain)
        self.assertEqual(text.plain.count("█"), 10)
        self.assertEqual(sysdeck.bar(0, 10, theme, label=False).plain, "░" * 10)


class SmokeTest(unittest.TestCase):
    def test_render_runs(self):
        from rich.console import Console

        sampler = sysdeck.Sampler()
        sampler.sample()
        console = Console(width=110, record=True, force_terminal=False, file=io.StringIO())
        console.print(sysdeck.render(sampler, sysdeck.THEMES["cyber"], 5, 110))
        out = console.export_text()
        self.assertIn("SYSDECK", out)
        self.assertIn("TOP PROCESSES", out)


if __name__ == "__main__":
    unittest.main()
