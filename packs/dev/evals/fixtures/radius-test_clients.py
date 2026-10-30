import unittest

from clients import render_clients


class RenderTest(unittest.TestCase):
    def test_sorted_blocks(self) -> None:
        out = render_clients({"bras-mrs-02": "10.20.1.12", "bras-lyo-01": "10.20.0.11"})
        self.assertTrue(out.startswith("client bras-lyo-01 {"))
        self.assertIn("ipaddr = 10.20.1.12", out)


if __name__ == "__main__":
    unittest.main()
