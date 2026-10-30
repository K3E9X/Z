import unittest

from render import availability


class AvailabilityTest(unittest.TestCase):
    def test_percentage(self) -> None:
        self.assertEqual(availability(9_990, 10_000), "99.90 %")


if __name__ == "__main__":
    unittest.main()
