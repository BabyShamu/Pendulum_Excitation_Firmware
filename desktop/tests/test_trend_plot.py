import unittest

from pendulum_workbench.ui.trend_plot import TrendPlot


class TrendPlotTests(unittest.TestCase):
    def test_x_coordinates_follow_irregular_elapsed_times(self) -> None:
        start = TrendPlot.map_elapsed_to_x(1.0, 1.0, 1.4, 10.0, 400.0)
        middle = TrendPlot.map_elapsed_to_x(1.1, 1.0, 1.4, 10.0, 400.0)
        end = TrendPlot.map_elapsed_to_x(1.4, 1.0, 1.4, 10.0, 400.0)

        self.assertEqual(start, 10.0)
        self.assertAlmostEqual(middle, 110.0)
        self.assertEqual(end, 410.0)


if __name__ == "__main__":
    unittest.main()