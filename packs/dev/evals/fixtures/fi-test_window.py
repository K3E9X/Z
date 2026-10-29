import unittest

from window import in_window


class WindowTest(unittest.TestCase):
    def test_daytime_window(self) -> None:
        self.assertTrue(in_window(10, 9, 12))
        self.assertFalse(in_window(12, 9, 12))


if __name__ == "__main__":
    unittest.main()
