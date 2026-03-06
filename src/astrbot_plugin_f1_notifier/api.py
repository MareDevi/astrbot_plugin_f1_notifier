"""F1 API client module.

Wraps:
  - Jolpica-F1 (Ergast mirror): https://api.jolpi.ca/ergast/f1/
  - OpenF1: https://api.openf1.org/v1/

All public functions return ``ApiResult[T]`` — either ``Success(value=...)``
or ``Failure(error=...)``.  Callers use ``match`` / ``case`` to branch:

    result = await get_race_result()
    match result:
        case Success(value=race):  ...
        case Failure(error=err):   ...
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import aiohttp

from .models import (
    Success,
    Failure,
    JolpicaRace,
    JolpicaDriverStanding,
    JolpicaConstructorStanding,
    OpenF1Session,
    OpenF1Driver,
    OpenF1Position,
    OpenF1SessionResult,
    OpenF1Meeting,
)

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .models import (
        ScheduleResult,
        RaceResult,
        StandingsResult,
        ConstructorStandingsResult,
        SessionResult,
        SessionResultsResult,
        DriversResult,
        GridResult,
        MeetingResult,
    )

JOLPICA_BASE = "https://api.jolpi.ca/ergast/f1"
OPENF1_BASE = "https://api.openf1.org/v1"

_SESSION_LOCK: asyncio.Lock | None = None
_SESSION_LOCK_LOOP: asyncio.AbstractEventLoop | None = None
_CLIENT_SESSION: aiohttp.ClientSession | None = None


def _ensure_lock() -> asyncio.Lock:
    """Return the session lock, recreating it when the event loop has changed."""
    global _SESSION_LOCK, _SESSION_LOCK_LOOP
    running_loop = asyncio.get_running_loop()
    if _SESSION_LOCK is None or _SESSION_LOCK_LOOP is not running_loop:
        _SESSION_LOCK = asyncio.Lock()
        _SESSION_LOCK_LOOP = running_loop
    return _SESSION_LOCK


async def _get_session() -> aiohttp.ClientSession:
    global _CLIENT_SESSION
    lock = _ensure_lock()
    async with lock:
        if _CLIENT_SESSION is None or _CLIENT_SESSION.closed:
            _CLIENT_SESSION = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15),
                headers={"Accept": "application/json"},
            )
    return _CLIENT_SESSION


async def close_session() -> None:
    global _CLIENT_SESSION
    lock = _ensure_lock()
    async with lock:
        if _CLIENT_SESSION and not _CLIENT_SESSION.closed:
            await _CLIENT_SESSION.close()
            _CLIENT_SESSION = None


# ──────────────────────────────────────────────────────────────────────────────
# Low-level HTTP helpers (return raw data — no model parsing here)
# ──────────────────────────────────────────────────────────────────────────────


async def _jolpica_get(path: str) -> dict[str, Any]:
    session = await _get_session()
    url = f"{JOLPICA_BASE}{path}"
    async with session.get(url) as resp:
        resp.raise_for_status()
        return await resp.json(content_type=None)


async def _openf1_get(path: str, params: dict | None = None) -> list[dict]:
    session = await _get_session()
    url = f"{OPENF1_BASE}{path}"
    async with session.get(url, params=params) as resp:
        resp.raise_for_status()
        return await resp.json(content_type=None)


# ──────────────────────────────────────────────────────────────────────────────
# Datetime helpers
# ──────────────────────────────────────────────────────────────────────────────


def _parse_iso_datetime(s: str) -> datetime | None:
    """Parse ISO 8601 string → timezone-aware datetime, or None on failure."""
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError):
        return None


# ──────────────────────────────────────────────────────────────────────────────
# Jolpica public API
# ──────────────────────────────────────────────────────────────────────────────


async def get_current_schedule(season: int | str = "current") -> ScheduleResult:
    """Return all races for a season (default: current)."""
    try:
        raw = await _jolpica_get(f"/{season}.json?limit=30")
        races_raw: list[dict] = raw["MRData"]["RaceTable"]["Races"]
        if not races_raw:
            return Failure(error="empty schedule")
        return Success(value=[JolpicaRace.model_validate(r) for r in races_raw])
    except (aiohttp.ClientError, KeyError, ValueError, TypeError, TimeoutError) as exc:
        return Failure(error=str(exc))


async def get_race_result(
    round_number: int | str = "last", season: int | str = "current"
) -> RaceResult:
    """Return race result for a given round ('last' for latest finished race)."""
    try:
        raw = await _jolpica_get(f"/{season}/{round_number}/results.json?limit=30")
        races_raw: list[dict] = raw["MRData"]["RaceTable"]["Races"]
        match races_raw:
            case [first, *_]:
                return Success(value=JolpicaRace.model_validate(first))
            case _:
                return Failure(error="no race data")
    except (aiohttp.ClientError, KeyError, ValueError, TypeError, TimeoutError) as exc:
        return Failure(error=str(exc))


async def get_qualifying_result(
    round_number: int | str = "last", season: int | str = "current"
) -> RaceResult:
    """Return qualifying result for a given round."""
    try:
        raw = await _jolpica_get(f"/{season}/{round_number}/qualifying.json?limit=30")
        races_raw: list[dict] = raw["MRData"]["RaceTable"]["Races"]
        match races_raw:
            case [first, *_]:
                return Success(value=JolpicaRace.model_validate(first))
            case _:
                return Failure(error="no qualifying data")
    except (aiohttp.ClientError, KeyError, ValueError, TypeError, TimeoutError) as exc:
        return Failure(error=str(exc))


async def get_sprint_result(
    round_number: int | str, season: int | str = "current"
) -> RaceResult:
    """Return sprint race result for a given round."""
    try:
        raw = await _jolpica_get(f"/{season}/{round_number}/sprint.json?limit=30")
        races_raw: list[dict] = raw["MRData"]["RaceTable"]["Races"]
        match races_raw:
            case [first, *_]:
                return Success(value=JolpicaRace.model_validate(first))
            case _:
                return Failure(error="no sprint data")
    except (aiohttp.ClientError, KeyError, ValueError, TypeError, TimeoutError) as exc:
        return Failure(error=str(exc))


async def get_driver_standings(season: int | str = "current") -> StandingsResult:
    """Return driver championship standings for a season."""
    try:
        raw = await _jolpica_get(f"/{season}/driverStandings.json")
        tables: list[dict] = raw["MRData"]["StandingsTable"]["StandingsLists"]
        match tables:
            case [first, *_]:
                standings = [
                    JolpicaDriverStanding.model_validate(e)
                    for e in first["DriverStandings"]
                ]
                return Success(value=standings)
            case _:
                return Failure(error="no standings data")
    except (aiohttp.ClientError, KeyError, ValueError, TypeError, TimeoutError) as exc:
        return Failure(error=str(exc))


async def get_constructor_standings(
    season: int | str = "current",
) -> ConstructorStandingsResult:
    """Return constructor championship standings for a season."""
    try:
        raw = await _jolpica_get(f"/{season}/constructorStandings.json")
        tables: list[dict] = raw["MRData"]["StandingsTable"]["StandingsLists"]
        match tables:
            case [first, *_]:
                standings = [
                    JolpicaConstructorStanding.model_validate(e)
                    for e in first["ConstructorStandings"]
                ]
                return Success(value=standings)
            case _:
                return Failure(error="no constructor standings data")
    except (aiohttp.ClientError, KeyError, ValueError, TypeError, TimeoutError) as exc:
        return Failure(error=str(exc))


# ──────────────────────────────────────────────────────────────────────────────
# OpenF1 public API
# ──────────────────────────────────────────────────────────────────────────────


async def get_latest_session(session_name: str = "Race") -> SessionResult:
    """Return the most recently *started* OpenF1 session with given name.

    Filters out sessions that haven't started yet so we never return a future
    session_key (e.g. Abu Dhabi when the current race is Australia).
    """
    try:
        year = datetime.now(timezone.utc).year
        results = await _openf1_get(
            "/sessions",
            params={"session_name": session_name, "year": year},
        )
        now = datetime.now(timezone.utc)
        past = [
            r for r in results
            if (dt := _parse_iso_datetime(r.get("date_start", ""))) is not None
            and dt <= now
        ]
        match past:
            case []:
                return Failure(error="no past sessions found")
            case past_list:
                past_list.sort(
                    key=lambda r: _parse_iso_datetime(r.get("date_start", ""))
                    or datetime.min.replace(tzinfo=timezone.utc)
                )
                return Success(value=OpenF1Session.model_validate(past_list[-1]))
    except (aiohttp.ClientError, KeyError, ValueError, TypeError, TimeoutError) as exc:
        return Failure(error=str(exc))


# Maps user-friendly fp_number → OpenF1 session_name
FP_SESSION_NAMES = {
    "1": "Practice 1",
    "fp1": "Practice 1",
    "2": "Practice 2",
    "fp2": "Practice 2",
    "3": "Practice 3",
    "fp3": "Practice 3",
}


async def get_practice_session(
    fp_number: str = "1", year: int | None = None
) -> SessionResult:
    """Return the most recently completed OpenF1 practice session.

    fp_number: '1', '2', or '3' (also accepts 'fp1', 'fp2', 'fp3')
    year: season year, defaults to current year
    """
    try:
        session_name = FP_SESSION_NAMES.get(fp_number.lower(), "Practice 1")
        if year is None:
            year = datetime.now(timezone.utc).year
        results = await _openf1_get(
            "/sessions",
            params={"session_name": session_name, "year": year},
        )
        now = datetime.now(timezone.utc)
        past = [
            r for r in results
            if (dt := _parse_iso_datetime(r.get("date_start", ""))) is not None
            and dt <= now
        ]
        match past:
            case []:
                return Failure(error=f"no past FP{fp_number} session found")
            case past_list:
                past_list.sort(
                    key=lambda r: _parse_iso_datetime(r.get("date_start", ""))
                    or datetime.min.replace(tzinfo=timezone.utc)
                )
                return Success(value=OpenF1Session.model_validate(past_list[-1]))
    except (aiohttp.ClientError, KeyError, ValueError, TypeError, TimeoutError) as exc:
        return Failure(error=str(exc))


async def get_drivers_for_session(session_key: int | str) -> DriversResult:
    """Return driver info for a given OpenF1 session key."""
    try:
        raw = await _openf1_get("/drivers", params={"session_key": session_key})
        drivers = [OpenF1Driver.model_validate(d) for d in raw]
        return Success(value=drivers)
    except (aiohttp.ClientError, KeyError, ValueError, TypeError, TimeoutError) as exc:
        return Failure(error=str(exc))


async def get_starting_grid(session_key: int | str) -> GridResult:
    """Return starting grid sorted by position.

    Returns earliest position entry per driver (their starting position).
    """
    try:
        raw = await _openf1_get("/position", params={"session_key": session_key})
        if not raw:
            return Success(value=[])
        # Keep only the first (earliest) entry per driver
        driver_first: dict[int, dict] = {}
        for entry in raw:
            drv = entry.get("driver_number")
            if drv is None:
                continue
            if drv not in driver_first:
                driver_first[drv] = entry
        grid = sorted(
            (OpenF1Position.model_validate(e) for e in driver_first.values()),
            key=lambda x: x.position,
        )
        return Success(value=grid)
    except (aiohttp.ClientError, KeyError, ValueError, TypeError, TimeoutError) as exc:
        return Failure(error=str(exc))


async def get_meeting_for_session(session_key: int | str) -> MeetingResult:
    """Return meeting info for an OpenF1 session key."""
    try:
        results = await _openf1_get("/meetings", params={"session_key": session_key})
        match results:
            case [first, *_]:
                return Success(value=OpenF1Meeting.model_validate(first))
            case _:
                return Failure(error="no meeting found")
    except (aiohttp.ClientError, KeyError, ValueError, TypeError, TimeoutError) as exc:
        return Failure(error=str(exc))


async def get_session_result(session_key: int | str) -> SessionResultsResult:
    """Return session result (standings) for a given OpenF1 session.

    Each entry: position, driver_number, duration (best lap s), gap_to_leader,
    number_of_laps.  Sorted by position ascending.
    """
    try:
        raw = await _openf1_get(
            "/session_result", params={"session_key": session_key}
        )
        results = sorted(
            (OpenF1SessionResult.model_validate(r) for r in raw),
            key=lambda x: x.position,
        )
        return Success(value=results)
    except (aiohttp.ClientError, KeyError, ValueError, TypeError, TimeoutError) as exc:
        return Failure(error=str(exc))
