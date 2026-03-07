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
from typing import TYPE_CHECKING, Any

import aiohttp

from .models import (
    F1QualifyingResult,
    F1RaceResult,
    F1RaceWeekend,
    F1SessionSlot,
    F1SprintResult,
    Failure,
    JolpicaConstructorStanding,
    JolpicaDriverStanding,
    OpenF1Driver,
    OpenF1Meeting,
    OpenF1Position,
    OpenF1Session,
    OpenF1SessionResult,
    Success,
)

if TYPE_CHECKING:
    from .models import (
        ConstructorStandingsResult,
        DriversResult,
        GridResult,
        MeetingResult,
        RaceResult,
        ScheduleResult,
        SessionResult,
        SessionResultsResult,
        StandingsResult,
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


async def _openf1_get(
    path: str, params: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    session = await _get_session()
    url = f"{OPENF1_BASE}{path}"
    async with session.get(url, params=params) as resp:
        resp.raise_for_status()
        return await resp.json(content_type=None)


# ──────────────────────────────────────────────────────────────────────────────
# Datetime helpers
# ──────────────────────────────────────────────────────────────────────────────


def _parse_iso_datetime(s: str) -> datetime | None:
    """Parse ISO 8601 string → timezone-aware UTC datetime, or None."""
    try:
        return datetime.fromisoformat(s)
    except (ValueError, TypeError, AttributeError):
        return None


def _split_iso_dt(ds: str) -> tuple[str, str]:
    """Split an ISO 8601 datetime into ('YYYY-MM-DD', 'HH:MM:SSZ').

    Normalises to UTC so downstream code can stay simple.
    """
    dt = _parse_iso_datetime(ds)
    if dt is None:
        return ds[:10], ""
    utc = dt.astimezone(timezone.utc)
    return utc.strftime("%Y-%m-%d"), utc.strftime("%H:%M:%SZ")


async def _empty_list() -> list:
    return []


def _secs_to_laptime(secs: float | None) -> str:
    """Convert seconds → '1:23.456' qualifying lap-time string, or '—'."""
    if not secs:
        return "—"
    m = int(secs) // 60
    s = secs - m * 60
    return f"{m}:{s:06.3f}"


def _secs_to_racetime(secs: float | None) -> str:
    """Convert seconds → '1:23:45.678' (or '23:45.678') race-time string."""
    if not secs:
        return "-"
    h = int(secs) // 3600
    m = (int(secs) % 3600) // 60
    s = secs % 60
    if h > 0:
        return f"{h}:{m:02d}:{s:06.3f}"
    return f"{m}:{s:06.3f}"


# Maps OpenF1 circuit_short_name → Jolpica circuit_id (for image lookup)
_OPENF1_CIRCUIT_MAP: dict[str, str] = {
    "Albert Park": "albert_park",
    "Melbourne": "albert_park",
    "Shanghai": "shanghai",
    "Suzuka": "suzuka",
    "Sakhir": "bahrain",
    "Bahrain": "bahrain",
    "Jeddah": "jeddah",
    "Miami": "miami",
    "Imola": "imola",
    "Monaco": "monaco",
    "Catalunya": "catalunya",
    "Barcelona": "catalunya",
    "Gilles Villeneuve": "villeneuve",
    "Montreal": "villeneuve",
    "Red Bull Ring": "red_bull_ring",
    "Spielberg": "red_bull_ring",
    "Silverstone": "silverstone",
    "Spa-Francorchamps": "spa",
    "Hungaroring": "hungaroring",
    "Budapest": "hungaroring",
    "Zandvoort": "zandvoort",
    "Monza": "monza",
    "Baku": "baku",
    "Singapore": "marina_bay",
    "Marina Bay": "marina_bay",
    "Austin": "americas",
    "COTA": "americas",
    "Mexico City": "rodriguez",
    "Interlagos": "interlagos",
    "São Paulo": "interlagos",
    "Las Vegas": "vegas",
    "Lusail": "losail",
    "Yas Marina": "yas_marina",
    "Portimão": "portimao",
    "Istanbul": "istanbul",
    "Mugello": "mugello",
    "Sochi": "sochi",
    "Nürburgring": "nurburgring",
}


async def _get_openf1_round(
    session_name: str, round_number: int | str, year: int
) -> tuple[dict, int]:
    """Return (session_dict, 0-based_round_index) for the matching session.

    Finds all past sessions of *session_name* in *year*, sorts them by
    date_start, and returns the one at *round_number* (1-based) or the
    most recent when *round_number* == 'last'.

    Raises ValueError when no suitable session is found.
    """
    sessions_raw = await _openf1_get(
        "/sessions", params={"session_name": session_name, "year": year}
    )
    now = datetime.now(timezone.utc)
    past = [
        r
        for r in sessions_raw
        if (dt := _parse_iso_datetime(r.get("date_start", ""))) is not None
        and dt <= now
    ]
    if not past:
        raise ValueError(f"no past {session_name!r} sessions for {year}")
    past.sort(
        key=lambda r: _parse_iso_datetime(r.get("date_start", ""))
        or datetime.min.replace(tzinfo=timezone.utc)
    )
    if round_number == "last":
        return past[-1], len(past) - 1
    idx = int(round_number) - 1
    if 0 <= idx < len(past):
        return past[idx], idx
    raise ValueError(f"round {round_number} not found in {session_name!r} for {year}")


async def _qualifying_from_openf1(
    round_number: int | str, year: int
) -> "RaceResult":
    """Fetch qualifying result from OpenF1."""
    chosen, chosen_idx = await _get_openf1_round("Qualifying", round_number, year)
    session_key = chosen["session_key"]

    meeting_key = chosen.get("meeting_key")
    results_raw, drivers_raw, meetings_raw = await asyncio.gather(
        _openf1_get("/session_result", params={"session_key": session_key}),
        _openf1_get("/drivers", params={"session_key": session_key}),
        _openf1_get("/meetings", params={"meeting_key": meeting_key})
        if meeting_key
        else _empty_list(),
    )
    if not results_raw or not drivers_raw:
        raise ValueError("empty session_result or drivers from OpenF1")

    drivers_map: dict[int, dict] = {d["driver_number"]: d for d in drivers_raw}
    meeting: dict = meetings_raw[0] if meetings_raw else {}

    qual_results: list[F1QualifyingResult] = []
    for entry in sorted(results_raw, key=lambda x: x.get("position", 99)):
        driver_num = entry.get("driver_number")
        d = drivers_map.get(driver_num, {})

        duration = entry.get("duration")
        if isinstance(duration, list):
            q1_s = duration[0] if len(duration) > 0 else None
            q2_s = duration[1] if len(duration) > 1 else None
            q3_s = duration[2] if len(duration) > 2 else None
        else:
            q1_s, q2_s, q3_s = None, None, None

        first_name = d.get("first_name") or ""
        last_name = d.get("last_name") or f"#{driver_num}"
        qual_results.append(
            F1QualifyingResult(
                position=entry.get("position", 99),
                driver_name=f"{first_name} {last_name}".strip(),
                driver_first_name=first_name,
                driver_last_name=last_name,
                team_name=d.get("team_name") or "?",
                headshot_url=d.get("headshot_url") or None,
                q1=_secs_to_laptime(q1_s),
                q2=_secs_to_laptime(q2_s),
                q3=_secs_to_laptime(q3_s),
            )
        )

    circuit_short = chosen.get("circuit_short_name") or ""
    return Success(
        value=F1RaceWeekend(
            season=str(year),
            round=str(chosen_idx + 1),
            race_name=meeting.get("meeting_name") or chosen.get("location") or "?",
            circuit_id=_OPENF1_CIRCUIT_MAP.get(circuit_short, ""),
            circuit_name=circuit_short,
            locality=chosen.get("location") or "",
            country=chosen.get("country_name") or "",
            meeting_key=chosen.get("meeting_key") or 0,
            date=chosen.get("date_start", "")[:10],
            qualifying_results=qual_results,
        )
    )


async def _race_from_openf1(
    round_number: int | str, year: int
) -> "RaceResult":
    """Fetch race result from OpenF1."""
    chosen, chosen_idx = await _get_openf1_round("Race", round_number, year)
    session_key = chosen["session_key"]

    meeting_key = chosen.get("meeting_key")
    results_raw, drivers_raw, meetings_raw = await asyncio.gather(
        _openf1_get("/session_result", params={"session_key": session_key}),
        _openf1_get("/drivers", params={"session_key": session_key}),
        _openf1_get("/meetings", params={"meeting_key": meeting_key})
        if meeting_key
        else _empty_list(),
    )
    if not results_raw or not drivers_raw:
        raise ValueError("empty session_result or drivers from OpenF1")

    drivers_map: dict[int, dict] = {d["driver_number"]: d for d in drivers_raw}
    meeting: dict = meetings_raw[0] if meetings_raw else {}

    results_sorted = sorted(results_raw, key=lambda x: x.get("position", 99))
    leader_duration = results_sorted[0].get("duration") if results_sorted else None

    race_results: list[F1RaceResult] = []
    for entry in results_sorted:
        driver_num = entry.get("driver_number")
        d = drivers_map.get(driver_num, {})
        pos = entry.get("position", 99)

        if entry.get("dns"):
            status, time_val = "DNS", None
        elif entry.get("dsq"):
            status, time_val = "DSQ", None
        elif entry.get("dnf"):
            status, time_val = "DNF", None
        else:
            status = "Finished"
            gap = entry.get("gap_to_leader")
            if pos == 1:
                time_val = _secs_to_racetime(leader_duration)
            elif isinstance(gap, str):
                time_val = gap
            elif isinstance(gap, (int, float)) and gap > 0:
                time_val = f"+{gap:.3f}"
            else:
                time_val = "-"

        race_pts = int(entry.get("points") or 0)
        first_name = d.get("first_name") or ""
        last_name = d.get("last_name") or f"#{driver_num}"

        race_results.append(
            F1RaceResult(
                position=pos,
                driver_name=f"{first_name} {last_name}".strip(),
                driver_first_name=first_name,
                driver_last_name=last_name,
                team_name=d.get("team_name") or "?",
                headshot_url=d.get("headshot_url") or None,
                laps=str(entry.get("number_of_laps") or ""),
                status=status,
                time=time_val,
                points=str(race_pts),
            )
        )

    circuit_short = chosen.get("circuit_short_name") or ""
    return Success(
        value=F1RaceWeekend(
            season=str(year),
            round=str(chosen_idx + 1),
            race_name=meeting.get("meeting_name") or chosen.get("location") or "?",
            circuit_id=_OPENF1_CIRCUIT_MAP.get(circuit_short, ""),
            circuit_name=circuit_short,
            locality=chosen.get("location") or "",
            country=chosen.get("country_name") or "",
            meeting_key=chosen.get("meeting_key") or 0,
            date=chosen.get("date_start", "")[:10],
            race_results=race_results,
        )
    )


async def _sprint_from_openf1(
    round_number: int | str, year: int
) -> "RaceResult":
    """Fetch sprint race result from OpenF1."""
    chosen, chosen_idx = await _get_openf1_round("Sprint", round_number, year)
    session_key = chosen["session_key"]

    meeting_key = chosen.get("meeting_key")
    results_raw, drivers_raw, meetings_raw = await asyncio.gather(
        _openf1_get("/session_result", params={"session_key": session_key}),
        _openf1_get("/drivers", params={"session_key": session_key}),
        _openf1_get("/meetings", params={"meeting_key": meeting_key})
        if meeting_key
        else _empty_list(),
    )
    if not results_raw or not drivers_raw:
        raise ValueError("empty session_result or drivers from OpenF1")

    drivers_map: dict[int, dict] = {d["driver_number"]: d for d in drivers_raw}
    meeting: dict = meetings_raw[0] if meetings_raw else {}

    results_sorted = sorted(results_raw, key=lambda x: x.get("position", 99))
    leader_duration = results_sorted[0].get("duration") if results_sorted else None

    sprint_results: list[F1SprintResult] = []
    for entry in results_sorted:
        driver_num = entry.get("driver_number")
        d = drivers_map.get(driver_num, {})
        pos = entry.get("position", 99)

        if entry.get("dns"):
            status, time_val = "DNS", None
        elif entry.get("dsq"):
            status, time_val = "DSQ", None
        elif entry.get("dnf"):
            status, time_val = "DNF", None
        else:
            status = "Finished"
            gap = entry.get("gap_to_leader")
            if pos == 1:
                time_val = _secs_to_racetime(leader_duration)
            elif isinstance(gap, str):
                time_val = gap
            elif isinstance(gap, (int, float)) and gap > 0:
                time_val = f"+{gap:.3f}"
            else:
                time_val = "-"

        first_name = d.get("first_name") or ""
        last_name = d.get("last_name") or f"#{driver_num}"

        sprint_results.append(
            F1SprintResult(
                position=pos,
                driver_name=f"{first_name} {last_name}".strip(),
                driver_first_name=first_name,
                driver_last_name=last_name,
                team_name=d.get("team_name") or "?",
                headshot_url=d.get("headshot_url") or None,
                laps=str(entry.get("number_of_laps") or ""),
                status=status,
                time=time_val,
                points=str(int(entry.get("points") or 0)),
            )
        )

    circuit_short = chosen.get("circuit_short_name") or ""
    return Success(
        value=F1RaceWeekend(
            season=str(year),
            round=str(chosen_idx + 1),
            race_name=meeting.get("meeting_name") or chosen.get("location") or "?",
            circuit_id=_OPENF1_CIRCUIT_MAP.get(circuit_short, ""),
            circuit_name=circuit_short,
            locality=chosen.get("location") or "",
            country=chosen.get("country_name") or "",
            meeting_key=chosen.get("meeting_key") or 0,
            date=chosen.get("date_start", "")[:10],
            sprint_results=sprint_results,
        )
    )


async def get_current_schedule(season: int | str = "current") -> ScheduleResult:
    """Return all race weekends for a season using OpenF1 meetings + sessions."""
    try:
        year = datetime.now(timezone.utc).year if season == "current" else int(season)
        meetings_raw, sessions_raw = await asyncio.gather(
            _openf1_get("/meetings", params={"year": year}),
            _openf1_get("/sessions", params={"year": year}),
        )
        if not meetings_raw:
            return Failure(error="empty schedule")

        # Group sessions by meeting_key
        sessions_by_meeting: dict[int, list[dict]] = {}
        for s in sessions_raw:
            mk = s.get("meeting_key")
            if mk:
                sessions_by_meeting.setdefault(mk, []).append(s)

        _SESSION_FIELD_MAP = {
            "Practice 1": "first_practice",
            "Practice 2": "second_practice",
            "Practice 3": "third_practice",
            "Qualifying": "qualifying",
            "Sprint": "sprint",
            "Sprint Qualifying": "sprint_qualifying",
            "Sprint Shootout": "sprint_qualifying",
        }

        weekends: list[F1RaceWeekend] = []
        sorted_meetings = sorted(
            meetings_raw, key=lambda x: x.get("date_start", "")
        )
        # Filter out non-race meetings (e.g. Pre-Season Testing)
        sorted_meetings = [
            m for m in sorted_meetings
            if "Grand Prix" in (m.get("meeting_name") or "")
        ]
        for idx, m in enumerate(sorted_meetings, start=1):
            mk = m.get("meeting_key")
            circuit_short = m.get("circuit_short_name") or ""

            kwargs: dict[str, Any] = {
                "season": str(year),
                "round": str(idx),
                "race_name": m.get("meeting_name") or "",
                "circuit_id": _OPENF1_CIRCUIT_MAP.get(circuit_short, ""),
                "circuit_name": circuit_short,
                "locality": m.get("location") or "",
                "country": m.get("country_name") or "",
                "country_code": m.get("country_code") or "",
                "meeting_key": mk or 0,
            }

            for s in sessions_by_meeting.get(mk, []):
                sn = s.get("session_name", "")
                date_part, time_part = _split_iso_dt(s.get("date_start") or "")
                date_end_raw = s.get("date_end") or ""

                field = _SESSION_FIELD_MAP.get(sn)
                if field:
                    kwargs[field] = F1SessionSlot(
                        date=date_part, time=time_part, date_end=date_end_raw
                    )
                elif sn == "Race":
                    kwargs["date"] = date_part
                    kwargs["time"] = time_part
                    kwargs["race_date_end"] = date_end_raw

            weekends.append(F1RaceWeekend(**kwargs))

        if not weekends:
            return Failure(error="empty schedule")
        return Success(value=weekends)
    except (
        aiohttp.ClientError,
        KeyError,
        ValueError,
        TypeError,
        asyncio.TimeoutError,
    ) as exc:
        return Failure(error=str(exc))


async def get_race_result(
    round_number: int | str = "last", season: int | str = "current"
) -> RaceResult:
    """Return race result from OpenF1."""
    year = datetime.now(timezone.utc).year if season == "current" else int(season)
    try:
        return await _race_from_openf1(round_number, year)
    except (
        aiohttp.ClientError,
        KeyError,
        ValueError,
        TypeError,
        asyncio.TimeoutError,
    ) as exc:
        return Failure(error=str(exc))


async def get_qualifying_result(
    round_number: int | str = "last", season: int | str = "current"
) -> RaceResult:
    """Return qualifying result from OpenF1."""
    year = datetime.now(timezone.utc).year if season == "current" else int(season)
    try:
        return await _qualifying_from_openf1(round_number, year)
    except (
        aiohttp.ClientError,
        KeyError,
        ValueError,
        TypeError,
        asyncio.TimeoutError,
    ) as exc:
        return Failure(error=str(exc))


async def get_sprint_result(
    round_number: int | str, season: int | str = "current"
) -> RaceResult:
    """Return sprint race result from OpenF1."""
    year = datetime.now(timezone.utc).year if season == "current" else int(season)
    try:
        return await _sprint_from_openf1(round_number, year)
    except (
        aiohttp.ClientError,
        KeyError,
        ValueError,
        TypeError,
        asyncio.TimeoutError,
    ) as exc:
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
    except (
        aiohttp.ClientError,
        KeyError,
        ValueError,
        TypeError,
        asyncio.TimeoutError,
    ) as exc:
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
    except (
        aiohttp.ClientError,
        KeyError,
        ValueError,
        TypeError,
        asyncio.TimeoutError,
    ) as exc:
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
            r
            for r in results
            if (dt := _parse_iso_datetime(r.get("date_start", ""))) is not None
            and dt <= now
        ]
        match past:
            case []:
                return Failure(error="no past sessions found")
            case past_list:
                past_list.sort(
                    key=lambda r: (
                        _parse_iso_datetime(r.get("date_start", ""))
                        or datetime.min.replace(tzinfo=timezone.utc)
                    )
                )
                return Success(value=OpenF1Session.model_validate(past_list[-1]))
    except (
        aiohttp.ClientError,
        KeyError,
        ValueError,
        TypeError,
        asyncio.TimeoutError,
    ) as exc:
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
            r
            for r in results
            if (dt := _parse_iso_datetime(r.get("date_start", ""))) is not None
            and dt <= now
        ]
        match past:
            case []:
                return Failure(error=f"no past FP{fp_number} session found")
            case past_list:
                past_list.sort(
                    key=lambda r: (
                        _parse_iso_datetime(r.get("date_start", ""))
                        or datetime.min.replace(tzinfo=timezone.utc)
                    )
                )
                return Success(value=OpenF1Session.model_validate(past_list[-1]))
    except (
        aiohttp.ClientError,
        KeyError,
        ValueError,
        TypeError,
        asyncio.TimeoutError,
    ) as exc:
        return Failure(error=str(exc))


async def get_drivers_for_session(session_key: int | str) -> DriversResult:
    """Return driver info for a given OpenF1 session key."""
    try:
        raw = await _openf1_get("/drivers", params={"session_key": session_key})
        drivers = [OpenF1Driver.model_validate(d) for d in raw]
        return Success(value=drivers)
    except (
        aiohttp.ClientError,
        KeyError,
        ValueError,
        TypeError,
        asyncio.TimeoutError,
    ) as exc:
        return Failure(error=str(exc))


async def get_starting_grid(session_key: int | str) -> GridResult:
    """Return starting grid sorted by position using OpenF1 /starting_grid."""
    try:
        raw = await _openf1_get(
            "/starting_grid", params={"session_key": session_key}
        )
        if not raw:
            return Success(value=[])
        grid = sorted(
            (OpenF1Position.model_validate(e) for e in raw),
            key=lambda x: x.position,
        )
        return Success(value=grid)
    except (
        aiohttp.ClientError,
        KeyError,
        ValueError,
        TypeError,
        asyncio.TimeoutError,
    ) as exc:
        return Failure(error=str(exc))


async def get_meeting_for_session(session_key: int | str) -> MeetingResult:
    """Return meeting info for an OpenF1 session key."""
    try:
        # First resolve the meeting_key from the session, then query meetings
        sessions = await _openf1_get("/sessions", params={"session_key": session_key})
        if not sessions:
            return Failure(error="no session found")
        meeting_key = sessions[0].get("meeting_key")
        if not meeting_key:
            return Failure(error="session has no meeting_key")
        results = await _openf1_get("/meetings", params={"meeting_key": meeting_key})
        match results:
            case [first, *_]:
                return Success(value=OpenF1Meeting.model_validate(first))
            case _:
                return Failure(error="no meeting found")
    except (
        aiohttp.ClientError,
        KeyError,
        ValueError,
        TypeError,
        asyncio.TimeoutError,
    ) as exc:
        return Failure(error=str(exc))


async def get_session_result(session_key: int | str) -> SessionResultsResult:
    """Return session result (standings) for a given OpenF1 session.

    Each entry: position, driver_number, duration (best lap s), gap_to_leader,
    number_of_laps.  Sorted by position ascending.
    """
    try:
        raw = await _openf1_get("/session_result", params={"session_key": session_key})
        results = sorted(
            (OpenF1SessionResult.model_validate(r) for r in raw),
            key=lambda x: x.position,
        )
        return Success(value=results)
    except (
        aiohttp.ClientError,
        KeyError,
        ValueError,
        TypeError,
        asyncio.TimeoutError,
    ) as exc:
        return Failure(error=str(exc))
