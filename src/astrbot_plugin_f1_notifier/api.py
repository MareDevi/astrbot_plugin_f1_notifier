"""Asynchronous F1 clients with bounded caches and source-specific pacing."""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections import OrderedDict
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import aiohttp

from .models import SESSION_NAMES, Race, Session, Table, parse_datetime

logger = logging.getLogger(__name__)

OPENF1_NAMES = {
    "practice1": "Practice 1",
    "practice2": "Practice 2",
    "practice3": "Practice 3",
    "sprint_qualifying": "Sprint Qualifying",
    "qualifying": "Qualifying",
    "sprint": "Sprint",
    "race": "Race",
}


class DataError(Exception):
    """A data provider is temporarily unavailable or returned invalid data."""


class F1Client:
    """Own the HTTP session, cache, and request budget for one plugin instance.

    Args:
        jolpica_base: Jolpica API root.
        openf1_base: OpenF1 API root.
    """

    def __init__(
        self,
        jolpica_base: str = "https://api.jolpi.ca/ergast/f1",
        openf1_base: str = "https://api.openf1.org/v1",
    ) -> None:
        self.jolpica_base = jolpica_base
        self.openf1_base = openf1_base
        self._session: aiohttp.ClientSession | None = None
        self._cache: OrderedDict[tuple, tuple[float, Any]] = OrderedDict()
        self._locks: dict[str, asyncio.Lock] = {}
        self._next_request: dict[str, float] = {}
        self._retry_at: dict[str, float] = {}

    async def open(self) -> None:
        """Open one reusable connection pool during plugin initialization."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=20),
                headers={
                    "Accept": "application/json",
                    "User-Agent": "AstrBot-F1-Notifier/2.0",
                },
            )

    async def close(self) -> None:
        """Release connections and discard instance-owned response data."""
        if self._session is not None:
            await self._session.close()
            self._session = None
        self._cache.clear()

    async def _get(
        self, base: str, path: str, params: dict | None = None, ttl: float = 30
    ) -> Any:
        """Fetch JSON with cache coalescing and a per-provider request budget.

        Args:
            base: Provider API root.
            path: Endpoint relative to the root.
            params: Query parameters, included in the cache identity.
            ttl: Maximum successful-response cache lifetime in seconds.

        Returns:
            Decoded JSON object or list.

        Raises:
            DataError: On rate limiting, HTTP errors, or invalid JSON.
        """
        key = (base, path, tuple(sorted((params or {}).items())))
        async with self._locks.setdefault(base, asyncio.Lock()):
            now = time.monotonic()
            cached = self._cache.get(key)
            if cached and cached[0] > now:
                self._cache.move_to_end(key)
                return cached[1]
            if self._retry_at.get(base, 0) > now:
                raise DataError("数据源限流，请稍后重试。")
            await asyncio.sleep(max(0, self._next_request.get(base, 0) - now))
            if self._session is None or self._session.closed:
                raise DataError("数据客户端尚未就绪，请稍后重试。")
            # OpenF1's public tier permits 30 requests/minute. Pace all callers,
            # including interactive commands, rather than only the scheduler.
            interval = 2.1 if base == self.openf1_base else 0.6
            self._next_request[base] = time.monotonic() + interval
            try:
                async with self._session.get(
                    f"{base}{path}", params=params
                ) as response:
                    if response.status == 404 and base == self.openf1_base:
                        return []
                    if response.status == 429:
                        retry = response.headers.get("Retry-After", "60")
                        try:
                            delay = float(retry)
                        except ValueError:
                            try:
                                delay = (
                                    parsedate_to_datetime(retry)
                                    - datetime.now(timezone.utc)
                                ).total_seconds()
                            except (ValueError, TypeError, OverflowError):
                                delay = 60
                        if not math.isfinite(delay):
                            delay = 60
                        self._retry_at[base] = time.monotonic() + max(1, delay)
                        raise DataError("数据源限流，请稍后重试。")
                    response.raise_for_status()
                    data = await response.json()
                    if not isinstance(data, (dict, list)):
                        raise DataError("数据源返回了无效内容，请稍后重试。")
            except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
                raise DataError("数据源暂时不可用，请稍后重试。") from exc
            if data and ttl > 0:
                self._cache[key] = (time.monotonic() + ttl, data)
                self._cache.move_to_end(key)
                while len(self._cache) > 128:
                    self._cache.popitem(last=False)
            return data

    async def schedule(self, season: int) -> list[Race]:
        """Fetch complete known weekends for an explicit season.

        Args:
            season: Championship year.

        Returns:
            Weekends sorted chronologically; unknown race times are omitted.

        Raises:
            DataError: If the provider fails or the response has no race table.
        """
        data = await self._get(
            self.jolpica_base, f"/{season}.json", {"limit": 100}, ttl=1800
        )
        try:
            rows = data["MRData"]["RaceTable"]["Races"]
            races = []
            for raw in rows:
                try:
                    race = Race.from_jolpica(raw)
                    if race.season == season:
                        races.append(race)
                except (KeyError, ValueError, TypeError):
                    logger.warning(
                        "Skipping race with incomplete schedule: %s", raw.get("round")
                    )
        except (KeyError, TypeError) as exc:
            raise DataError("赛程数据格式异常，请稍后重试。") from exc
        # Jolpica supplies official round identities. OpenF1 can supply more
        # precise starts and ends without guessing rounds from meeting order.
        if season >= 2023 and races:
            try:
                timings = await self._get(
                    self.openf1_base, "/sessions", {"year": season}, ttl=300
                )
            except DataError:
                logger.warning("OpenF1 timing unavailable; keeping published schedule")
                timings = []
            if isinstance(timings, list):
                updated = []
                for race in races:
                    sessions = []
                    for slot in race.sessions:
                        candidates = []
                        names = {OPENF1_NAMES[slot.key]}
                        if slot.key == "sprint_qualifying":
                            names.add("Sprint Shootout")
                        for row in timings:
                            if row.get("is_cancelled") is True:
                                continue
                            try:
                                start = parse_datetime(row["date_start"])
                                end = (
                                    parse_datetime(row["date_end"])
                                    if row.get("date_end")
                                    else None
                                )
                                delta = abs((start - slot.start).total_seconds())
                                if (
                                    row.get("session_name") in names
                                    and delta <= 6 * 3600
                                    and (end is None or end > start)
                                ):
                                    candidates.append(
                                        (delta, Session(slot.key, start, end))
                                    )
                            except (KeyError, ValueError, TypeError):
                                continue
                        sessions.append(
                            min(candidates, key=lambda item: item[0])[1]
                            if candidates
                            else slot
                        )
                    updated.append(
                        replace(
                            race,
                            sessions=tuple(sorted(sessions, key=lambda s: s.start)),
                        )
                    )
                races = updated
        return sorted(races, key=lambda race: race.start)

    async def openf1_session(self, race: Race, kind: str) -> dict | None:
        """Match a session to its expected start rather than using latest.

        Args:
            race: Exact championship weekend.
            kind: Normalized session key.

        Returns:
            A matching session within six hours, or None if unpublished.

        Raises:
            DataError: If OpenF1 is unavailable or malformed.
        """
        slot = next((s for s in race.sessions if s.key == kind), None)
        if slot is None or race.season < 2023:
            return None
        names = {OPENF1_NAMES[kind]}
        if kind == "sprint_qualifying":
            names.add("Sprint Shootout")
        rows = await self._get(
            self.openf1_base,
            "/sessions",
            {
                "year": race.season,
                "date_start>": (slot.start - timedelta(hours=6)).isoformat(),
                "date_start<": (slot.start + timedelta(hours=6)).isoformat(),
            },
            ttl=300,
        )
        if not isinstance(rows, list):
            raise DataError("场次数据格式异常，请稍后重试。")
        candidates = []
        for row in rows:
            if row.get("is_cancelled") is True:
                continue
            try:
                delta = abs(
                    (parse_datetime(row["date_start"]) - slot.start).total_seconds()
                )
                if row.get("session_name") in names and delta <= 6 * 3600:
                    candidates.append((delta, row))
            except (KeyError, ValueError, TypeError):
                continue
        return min(candidates, key=lambda item: item[0])[1] if candidates else None

    async def results(self, race: Race, kind: str) -> Table | None:
        """Read results for an exact season, round, and session.

        Args:
            race: Weekend whose results are requested.
            kind: Session key, or grid for the race starting grid.

        Returns:
            A display table, or None when official results are unpublished.

        Raises:
            DataError: On unavailable providers or malformed result payloads.
        """
        if kind.startswith("practice") or kind in ("grid", "sprint_qualifying"):
            return await self._openf1_results(race, kind)
        endpoint, field = {
            "race": ("results", "Results"),
            "qualifying": ("qualifying", "QualifyingResults"),
            "sprint": ("sprint", "SprintResults"),
        }[kind]
        data = await self._get(
            self.jolpica_base,
            f"/{race.season}/{race.round}/{endpoint}.json",
            {"limit": 100},
            ttl=30,
        )
        try:
            races = data["MRData"]["RaceTable"]["Races"]
            raw = next(
                (
                    r
                    for r in races
                    if str(r.get("season")) == str(race.season)
                    and str(r.get("round")) == str(race.round)
                ),
                None,
            )
            rows = raw.get(field, []) if raw else []
            if not rows:
                return None
            output = []
            for row in rows:
                driver = row.get("Driver") or {}
                name = (
                    f"{driver.get('givenName', '')} {driver.get('familyName', '')}".strip()
                    or driver.get("driverId", "—")
                )
                prefix = (
                    str(row.get("positionText") or row.get("position") or "—"),
                    name,
                    str((row.get("Constructor") or {}).get("name") or "—"),
                )
                if kind == "qualifying":
                    output.append(
                        prefix
                        + tuple(str(row.get(q) or "—") for q in ("Q1", "Q2", "Q3"))
                    )
                else:
                    detail = (
                        (row.get("Time") or {}).get("time") or row.get("status") or "—"
                    )
                    laps = row.get("laps")
                    output.append(
                        prefix
                        + (
                            str(laps) if laps is not None else "—",
                            str(detail),
                            str(row.get("points") or "0"),
                        )
                    )
            columns = (
                ("名次", "车手", "车队", "Q1", "Q2", "Q3")
                if kind == "qualifying"
                else ("名次", "车手", "车队", "圈数", "用时 / 状态", "积分")
            )
            return Table(
                f"{SESSION_NAMES[kind]}结果",
                f"{race.season} 第 {race.round} 站 · {race.name}",
                columns,
                tuple(output),
                "数据来源：Jolpica-F1 · 以官方最终分类为准",
                race.circuit_id,
            )
        except (KeyError, TypeError, AttributeError) as exc:
            raise DataError("比赛结果格式异常，请稍后重试。") from exc

    async def _openf1_results(self, race: Race, kind: str) -> Table | None:
        """Join a precisely matched OpenF1 session result with driver names.

        Args:
            race: Exact weekend.
            kind: Practice, sprint qualifying, or grid.

        Returns:
            A complete display table, or None while data is missing.

        Raises:
            DataError: If OpenF1 is unavailable or returns invalid rows.
        """
        session = await self.openf1_session(race, "race" if kind == "grid" else kind)
        if not session:
            return None
        params = {"session_key": session["session_key"]}
        rows = await self._get(
            self.openf1_base,
            "/starting_grid" if kind == "grid" else "/session_result",
            params,
            ttl=30,
        )
        if not isinstance(rows, list):
            raise DataError("场次结果格式异常，请稍后重试。")
        if not rows:
            return None
        drivers = await self._get(self.openf1_base, "/drivers", params, ttl=86400)
        if not isinstance(drivers, list):
            raise DataError("车手数据格式异常，请稍后重试。")
        by_number = {d.get("driver_number"): d for d in drivers}
        output = []
        for row in sorted(rows, key=lambda r: r.get("position") or 999):
            driver = by_number.get(row.get("driver_number"), {})
            name = driver.get("full_name") or f"#{row.get('driver_number', '—')}"
            duration = row.get("lap_duration" if kind == "grid" else "duration")
            if isinstance(duration, list):
                duration = next(
                    (
                        v
                        for v in reversed(duration)
                        if isinstance(v, (int, float)) and v > 0
                    ),
                    None,
                )
            detail = "—"
            if (
                isinstance(duration, (int, float))
                and math.isfinite(duration)
                and duration > 0
            ):
                minutes, seconds = divmod(round(duration * 1000), 60000)
                detail = f"{minutes}:{seconds / 1000:06.3f}"
            for flag in ("dsq", "dns", "dnf"):
                if row.get(flag):
                    detail = flag.upper()
                    break
            values = (
                str(row.get("position") or "—"),
                str(name),
                str(driver.get("team_name") or "—"),
                detail,
            )
            if kind != "grid":
                gap = row.get("gap_to_leader")
                if isinstance(gap, list):
                    gap = next((v for v in reversed(gap) if v is not None), None)
                gap_text = str(gap) if isinstance(gap, str) and gap else "—"
                if isinstance(gap, (int, float)) and math.isfinite(gap):
                    gap_text = "领跑" if gap == 0 else f"{gap:+.3f}"
                laps = row.get("number_of_laps")
                values += (gap_text, str(laps) if laps is not None else "—")
            output.append(values)
        title = "正赛发车顺序" if kind == "grid" else f"{SESSION_NAMES[kind]}结果"
        return Table(
            title,
            f"{race.season} 第 {race.round} 站 · {race.name}",
            ("名次", "车手", "车队", "圈速 / 状态")
            + (() if kind == "grid" else ("差距", "圈数")),
            tuple(output),
            "数据来源：OpenF1 · 数据可能延迟发布",
            race.circuit_id,
        )

    async def standings(self, season: int, category: str) -> Table | None:
        """Fetch championship standings without silently selecting another year.

        Args:
            season: Championship year.
            category: drivers or teams.

        Returns:
            Standings table, or None before the first published standings.

        Raises:
            DataError: On unavailable providers or malformed payloads.
        """
        teams = category == "teams"
        endpoint = "constructorStandings" if teams else "driverStandings"
        data = await self._get(
            self.jolpica_base, f"/{season}/{endpoint}.json", {"limit": 100}, ttl=300
        )
        try:
            lists = data["MRData"]["StandingsTable"]["StandingsLists"]
            if not lists:
                return None
            standing = lists[0]
            output = []
            for row in standing["ConstructorStandings" if teams else "DriverStandings"]:
                if teams:
                    name = row["Constructor"]["name"]
                    detail = ()
                else:
                    driver = row["Driver"]
                    name = f"{driver['givenName']} {driver['familyName']}"
                    detail = (
                        " / ".join(c["name"] for c in row.get("Constructors", []))
                        or "—",
                    )
                output.append(
                    (str(row.get("positionText") or row["position"]), name)
                    + detail
                    + (str(row["points"]), str(row["wins"]))
                )
            columns = (
                ("名次", "车队", "积分", "胜场")
                if teams
                else ("名次", "车手", "车队", "积分", "胜场")
            )
            return (
                Table(
                    "车队积分榜" if teams else "车手积分榜",
                    f"{season} 赛季 · 第 {standing.get('round', '—')} 站后",
                    columns,
                    tuple(output),
                    "数据来源：Jolpica-F1",
                )
                if output
                else None
            )
        except (KeyError, TypeError) as exc:
            raise DataError("积分榜数据格式异常，请稍后重试。") from exc
