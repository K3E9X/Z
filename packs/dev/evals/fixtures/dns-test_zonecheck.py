import unittest

from zonecheck import low_ttl_records


class LowTtlTest(unittest.TestCase):
    def test_flags_short_ttl(self) -> None:
        records = [{"name": "www.example.test.", "ttl": 60}, {"name": "mx1.example.test.", "ttl": 3600}]
        self.assertEqual(low_ttl_records(records), ["www.example.test."])


if __name__ == "__main__":
    unittest.main()
