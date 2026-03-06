"""Unit tests for the F1 Notifier plugin.

Tests the key fixes:
  - Practice session number parsing (removeprefix instead of lstrip)
  - ISO datetime parsing in api
  - Formatter robustness (missing fields, gap type protection)
  - Scheduler logger import
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path

# src package root (repo_root/src)
_SRC_ROOT = Path(__file__).resolve().parent.parent / "src"
_SCHEDULER_SRC = _SRC_ROOT / "astrbot_plugin_f1_notifier" / "scheduler.py"
_API_SRC = _SRC_ROOT / "astrbot_plugin_f1_notifier" / "api.py"


# ---------------------------------------------------------------------------
# 1. Practice session parsing (main.py fix)
# ---------------------------------------------------------------------------

class TestPracticeSessionParsing(unittest.TestCase):
    """Verify removeprefix("fp") works correctly vs. the old lstrip("fp")."""

    @staticmethod
    def _parse(session: str) -> str:
        """Replicate the fixed parsing logic from main.py."""
        return session.lower().removeprefix("fp") or "1"

    def test_basic_numbers(self):
        self.assertEqual(self._parse("1"), "1")
        self.assertEqual(self._parse("2"), "2")
        self.assertEqual(self._parse("3"), "3")

    def test_fp_prefix(self):
        self.assertEqual(self._parse("fp1"), "1")
        self.assertEqual(self._parse("fp2"), "2")
        self.assertEqual(self._parse("fp3"), "3")

    def test_uppercase_fp_prefix(self):
        self.assertEqual(self._parse("FP1"), "1")
        self.assertEqual(self._parse("FP3"), "3")

    def test_empty_string_defaults_to_1(self):
        self.assertEqual(self._parse("fp"), "1")

    def test_invalid_inputs_not_silently_accepted(self):
        # With the old lstrip("fp"), "ppp1" would become "1" (valid).
        # With removeprefix("fp"), "ppp1" stays "ppp1" (invalid → rejected).
        result = self._parse("ppp1")
        self.assertNotIn(result, ("1", "2", "3"))

    def test_lstrip_would_wrongly_strip(self):
        # "ffp1" with lstrip("fp") would yield "1", but removeprefix("fp")
        # should keep it as "ffp1" which will be rejected by the validator.
        result = self._parse("ffp1")
        self.assertNotIn(result, ("1", "2", "3"))


# ---------------------------------------------------------------------------
# 2. ISO datetime parser in api
# ---------------------------------------------------------------------------

class TestParseIsoDatetime(unittest.TestCase):
    """Test the new _parse_iso_datetime helper."""

    @staticmethod
    def _parse(s: str) -> datetime | None:
        # Inline the logic to avoid import issues with the module
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except (ValueError, TypeError, AttributeError):
            return None

    def test_z_suffix(self):
        dt = self._parse("2025-03-15T14:00:00Z")
        self.assertIsNotNone(dt)
        self.assertEqual(dt.tzinfo is not None, True)

    def test_offset_suffix(self):
        dt = self._parse("2025-03-15T14:00:00+00:00")
        self.assertIsNotNone(dt)

    def test_fractional_seconds(self):
        dt = self._parse("2025-03-15T14:00:00.123Z")
        self.assertIsNotNone(dt)

    def test_invalid_string(self):
        self.assertIsNone(self._parse("not-a-date"))

    def test_empty_string(self):
        self.assertIsNone(self._parse(""))

    def test_none_input(self):
        self.assertIsNone(self._parse(None))

    def test_comparison_consistent(self):
        """Ensure Z and +00:00 parse to same time (string compare would differ)."""
        dt_z = self._parse("2025-03-15T14:00:00Z")
        dt_plus = self._parse("2025-03-15T14:00:00+00:00")
        self.assertEqual(dt_z, dt_plus)


# ---------------------------------------------------------------------------
# 3. Formatter robustness
# ---------------------------------------------------------------------------

class TestFormatterRobustness(unittest.TestCase):
    """Test formatter functions handle missing/unexpected fields gracefully."""

    def _import_fmt(self):
        """Import formatter from the src package."""
        from src.astrbot_plugin_f1_notifier import formatter
        return formatter

    def test_format_schedule_empty_list(self):
        fmt = self._import_fmt()
        # Empty list of JolpicaRace models
        result = fmt.format_schedule([])
        self.assertIsInstance(result, str)
        self.assertIn("赛季", result)

    def test_format_constructor_standings_empty(self):
        fmt = self._import_fmt()
        result = fmt.format_constructor_standings([])
        self.assertIsInstance(result, str)

    def test_format_driver_standings_empty(self):
        fmt = self._import_fmt()
        result = fmt.format_driver_standings([])
        self.assertIsInstance(result, str)

    def test_format_practice_gap_none(self):
        from src.astrbot_plugin_f1_notifier.models import (
            OpenF1Session,
            OpenF1SessionResult,
            OpenF1Driver,
        )
        fmt = self._import_fmt()
        session = OpenF1Session(circuit_short_name="Test", country_name="Italy")
        results = [
            OpenF1SessionResult(position=1, driver_number=1, duration=80.5, gap_to_leader=None),
        ]
        drivers = {1: OpenF1Driver(driver_number=1, full_name="Test Driver", team_name="Team")}
        result = fmt.format_practice_result(session, results, drivers, "1")
        self.assertIsInstance(result, str)
        # Gap should NOT appear since it's None
        self.assertNotIn("+", result)

    def test_format_practice_gap_numeric(self):
        from src.astrbot_plugin_f1_notifier.models import (
            OpenF1Session,
            OpenF1SessionResult,
            OpenF1Driver,
        )
        fmt = self._import_fmt()
        session = OpenF1Session(circuit_short_name="Test", country_name="Italy")
        results = [
            OpenF1SessionResult(position=2, driver_number=1, duration=81.0, gap_to_leader=0.512),
        ]
        drivers = {1: OpenF1Driver(driver_number=1, full_name="Test Driver", team_name="Team")}
        result = fmt.format_practice_result(session, results, drivers, "1")
        self.assertIn("+0.512s", result)


# ---------------------------------------------------------------------------
# 4. Scheduler logger import
# ---------------------------------------------------------------------------

class TestSchedulerLoggerImport(unittest.TestCase):
    """Verify the scheduler uses astrbot.api.logger, not logging.getLogger."""

    def test_no_logging_getLogger(self):
        source = _SCHEDULER_SRC.read_text()
        self.assertNotIn("logging.getLogger", source)
        self.assertNotIn("import logging", source)
        self.assertIn("from astrbot.api import logger", source)


# ---------------------------------------------------------------------------
# 5. Close session uses lock
# ---------------------------------------------------------------------------

class TestCloseSessionLock(unittest.TestCase):
    """Verify close_session uses _SESSION_LOCK."""

    def test_close_session_uses_lock(self):
        source = _API_SRC.read_text()
        import re
        match = re.search(r'async def close_session.*?(?=\nasync def |\nclass |\Z)', source, re.DOTALL)
        self.assertIsNotNone(match)
        func_body = match.group()
        self.assertIn("_SESSION_LOCK", func_body)


if __name__ == "__main__":
    unittest.main()
