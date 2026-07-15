import unittest

from elevation_metrics import elevation_gain_loss


class ElevationMetricsTests(unittest.TestCase):
    def test_zero_smoothing_retains_raw_point_changes(self):
        ascent, descent = elevation_gain_loss([(0, 100), (10, 103), (20, 101), (30, 105)], 0)
        self.assertEqual(ascent, 7)
        self.assertEqual(descent, 2)

    def test_distance_smoothing_rejects_alternating_gps_noise(self):
        samples = []
        for distance in range(0, 1001, 5):
            true_height = 100 + distance / 5 if distance <= 500 else 200 - (distance - 500) / 5
            noise = 2.5 if (distance // 5) % 2 else -2.5
            samples.append((distance, true_height + noise))

        raw_ascent, raw_descent = elevation_gain_loss(samples, 0)
        ascent, descent = elevation_gain_loss(samples, 50)

        self.assertGreater(raw_ascent, 400)
        self.assertGreater(raw_descent, 400)
        self.assertAlmostEqual(ascent, 95, delta=10)
        self.assertAlmostEqual(descent, 95, delta=10)

    def test_result_is_independent_of_original_point_density(self):
        sparse = [(distance, distance / 10) for distance in range(0, 1001, 20)]
        dense = [(distance, distance / 10) for distance in range(0, 1001, 5)]
        sparse_result = elevation_gain_loss(sparse, 50)
        dense_result = elevation_gain_loss(dense, 50)
        self.assertAlmostEqual(sparse_result[0], dense_result[0], delta=0.5)
        self.assertAlmostEqual(sparse_result[1], dense_result[1], delta=0.5)


if __name__ == "__main__":
    unittest.main()
