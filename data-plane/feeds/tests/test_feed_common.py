import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from feed_common import TickerplantConnection


class FeedCommonTests(unittest.TestCase):
    def test_normalize_row_converts_datetimes_to_q_timestamp_literals(self):
        conn = TickerplantConnection("localhost", 5010, "test")
        ts = datetime(2024, 1, 2, 3, 4, 5, 678901, tzinfo=timezone.utc)

        normalized = conn._normalize_row([ts, "AAPL", 123.45, 100, "B", "XNAS", "s0"])

        self.assertEqual(normalized[0], "2024.01.02D03:04:05.678901000")
        self.assertEqual(normalized[1:], ["AAPL", 123.45, 100, "B", "XNAS", "s0"])


if __name__ == "__main__":
    unittest.main()
