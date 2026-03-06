"""F1 API client module.

Wraps:
  - Jolpica-F1 (Ergast mirror): https://api.jolpi.ca/ergast/f1/
  - OpenF1: https://api.openf1.org/v1/
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import aiohttp

JOLPICA_BASE = "https://api.jolpi.ca/ergast/f1"
OPENF1_BASE = "https://api.openf1.org/v1"

_SESSION_LOCK = asyncio.Lock()
_CLIENT_SESSION: aiohttp.ClientSession | None = None


async def _get_session() -> aiohttp.ClientSession:
    global _CLIENT_SESSION
    async with _SESSION_LOCK:
        if _CLIENT_SESSION is None or _CLIENT_SESSION.closed:
            _CLIENT_SESSION = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15),
                headers={"Accept": "application/json"},
            )
    return _CLIENT_SESSION


async def close_session() -> None:
    global _CLIENT_SESSION
    if _CLIENT_SESSION and not _CLIENT_SESSION.closed:
        await _CLIENT_SESSION.close()
        _CLIENT_SESSION = None


# ──────────────────────────────────────────────────────────────────────────────
# Jolpica helpers
# ──────────────────────────────────────────────────────────────────────────────


async def _jolpica_get(path: str) -> dict[str, Any]:
    session = await _get_session()
    url = f"{JOLPICA_BASE}{path}"
    async with session.get(url) as resp:
        resp.raise_for_status()
        return await resp.json(content_type=None)


async def get_current_schedule(season: int | str = "current") -> list[dict]:
    """Return all races for a season (default: current)."""
    data = await _jolpica_get(f"/{season}.json?limit=30")
    return data["MRData"]["RaceTable"]["Races"]


async def get_race_result(round_number: int | str = "last", season: int | str = "current") -> dict | None:
    """Return race result for a given round ('last' for latest finished race)."""
    data = await _jolpica_get(f"/{season}/{round_number}/results.json?limit=30")
    races = data["MRData"]["RaceTable"]["Races"]
    return races[0] if races else None


async def get_qualifying_result(round_number: int | str = "last", season: int | str = "current") -> dict | None:
    """Return qualifying result for a given round."""
    data = await _jolpica_get(f"/{season}/{round_number}/qualifying.json?limit=30")
    races = data["MRData"]["RaceTable"]["Races"]
    return races[0] if races else None


async def get_sprint_result(round_number: int | str, season: int | str = "current") -> dict | None:
    """Return sprint race result for a given round."""
    data = await _jolpica_get(f"/{season}/{round_number}/sprint.json?limit=30")
    races = data["MRData"]["RaceTable"]["Races"]
    return races[0] if races else None


async def get_driver_standings(season: int | str = "current") -> list[dict]:
    """Return driver championship standings for a season."""
    data = await _jolpica_get(f"/{season}/driverStandings.json")
    tables = data["MRData"]["StandingsTable"]["StandingsLists"]
    if not tables:
        return []
    return tables[0]["DriverStandings"]


async def get_constructor_standings(season: int | str = "current") -> list[dict]:
    """Return constructor championship standings for a season."""
    data = await _jolpica_get(f"/{season}/constructorStandings.json")
    tables = data["MRData"]["StandingsTable"]["StandingsLists"]
    if not tables:
        return []
    return tables[0]["ConstructorStandings"]


# ──────────────────────────────────────────────────────────────────────────────
# OpenF1 helpers
# ──────────────────────────────────────────────────────────────────────────────


async def _openf1_get(path: str, params: dict | None = None) -> list[dict]:
    session = await _get_session()
    url = f"{OPENF1_BASE}{path}"
    async with session.get(url, params=params) as resp:
        resp.raise_for_status()
        return await resp.json(content_type=None)


async def get_latest_session(session_name: str = "Race") -> dict | None:
    """Return the most recently started OpenF1 session with given name.

    Filters out sessions that haven't started yet so we never return
    a future session_key (e.g. Abu Dhabi when the current race is Australia).
    """
    year = datetime.now(timezone.utc).year
    results = await _openf1_get(
        "/sessions",
        params={"session_name": session_name, "year": year},
    )
    if not results:
        return None
    now_iso = datetime.now(timezone.utc).isoformat()
    past = [r for r in results if r.get("date_start", "") <= now_iso]
    return past[-1] if past else None


async def get_drivers_for_session(session_key: int | str) -> list[dict]:
    """Return driver info for a given OpenF1 session key."""
    return await _openf1_get("/drivers", params={"session_key": session_key})


async def get_starting_grid(session_key: int | str) -> list[dict]:
    """Return the starting grid (position data) for an OpenF1 race session.

    Returns a list of dicts sorted by position, one entry per driver.
    """
    positions = await _openf1_get(
        "/position", params={"session_key": session_key}
    )
    if not positions:
        return []
    # Pick the earliest position entry per driver (their starting position)
    driver_first: dict[int, dict] = {}
    for entry in positions:
        drv = entry.get("driver_number")
        if drv not in driver_first:
            driver_first[drv] = entry
    grid = sorted(driver_first.values(), key=lambda x: x.get("position", 99))
    return grid


async def get_meeting_for_session(session_key: int | str) -> dict | None:
    """Return meeting info for an OpenF1 session key."""
    results = await _openf1_get(
        "/meetings", params={"session_key": session_key}
    )
    return results[0] if results else None


# ─── Practice session helpers ─────────────────────────────────────────────────

# Maps user-friendly names to OpenF1 session_name values
FP_SESSION_NAMES = {
    "1": "Practice 1",
    "fp1": "Practice 1",
    "2": "Practice 2",
    "fp2": "Practice 2",
    "3": "Practice 3",
    "fp3": "Practice 3",
}


async def get_practice_session(fp_number: str = "1", year: int | None = None) -> dict | None:
    """Return the most recently completed OpenF1 practice session.

    fp_number: '1', '2', or '3'  (also accepts 'fp1', 'fp2', 'fp3')
    year: season year, defaults to current year

    Filters out sessions that haven't started yet so we return the
    actual last completed practice, not a future one in the same year.
    """
    session_name = FP_SESSION_NAMES.get(fp_number.lower(), "Practice 1")
    if year is None:
        year = datetime.now(timezone.utc).year
    results = await _openf1_get(
        "/sessions",
        params={"session_name": session_name, "year": year},
    )
    if not results:
        return None
    now_iso = datetime.now(timezone.utc).isoformat()
    past = [r for r in results if r.get("date_start", "") <= now_iso]
    return past[-1] if past else None


async def get_session_result(session_key: int | str) -> list[dict]:
    """Return the session result (standings) for a given OpenF1 session.

    Each entry has: position, driver_number, duration (best lap s), gap_to_leader, number_of_laps
    Sorted by position ascending.
    """
    results = await _openf1_get(
        "/session_result", params={"session_key": session_key}
    )
    return sorted(results, key=lambda x: x.get("position", 99))
