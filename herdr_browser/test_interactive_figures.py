"""Contracts for recorded coordinate axes and backwards-compatible figures."""
import copy
import unittest
from .reports import checked_patch, visual_content


class InteractiveFigureTests(unittest.TestCase):
    def chart(self):
        return dict(title="Recorded success", caption="Actual measurements", x_label="Updates",
                    y_label="%", x_maximum=100, y_maximum=100, gap=50,
                    axes=[dict(key="x", label="Updates"), dict(key="round", label="Rounds"),
                          dict(key="elapsed_seconds", label="Hours", divisor=3600)],
                    series=[dict(label="PC", points=[dict(x=50, y=50, round=1,
                        elapsed_seconds=12.5, episodes=10, successes=5, lo=20, hi=80)])])

    def test_axes_preserve_exact_recorded_coordinates_in_portable_export(self):
        chart = self.chart()
        checked_patch({"curves": [chart]})
        document = visual_content({"curves": [chart]})
        self.assertIn("data-chart=", document)
        self.assertIn("elapsed_seconds", document)
        self.assertIn("12.5", document)

    def test_legacy_and_missing_time_are_supported(self):
        chart = self.chart()
        chart["series"][0]["points"][0]["elapsed_seconds"] = None
        checked_patch({"curves": [chart]})
        chart.pop("axes")
        chart["series"][0]["points"] = [dict(x=50, y=50)]
        checked_patch({"curves": [chart]})

    def test_invalid_timestamps_counts_and_intervals_are_rejected(self):
        for key, value in (("elapsed_seconds", float("nan")), ("elapsed_seconds", -1),
                           ("hi", float("inf")), ("successes", 11), ("episodes", 0)):
            chart = self.chart()
            chart["series"][0]["points"][0][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                checked_patch({"curves": [chart]})

    def test_unknown_or_duplicate_coordinate_axes_are_rejected(self):
        for axes in ([dict(key="inferred_time", label="Time")],
                     [dict(key="x", label="Updates"), dict(key="x", label="Duplicate")]):
            chart = self.chart()
            chart["axes"] = axes
            with self.assertRaises(ValueError):
                checked_patch({"curves": [chart]})
