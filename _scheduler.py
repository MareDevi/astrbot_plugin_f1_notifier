"""Background scheduler for F1 notifications.

Runs a perpetual asyncio loop (polling every 60 s) and broadcasts
notifications to all subscribed sessions when F1 events fire.

Storage: uses AstrBot KV store (Star.put_kv_data / get_kv_data)
  - "f1_subscribers": list[str]   — subscribed session strings
  - "f1_state":       dict        — last notified round + events

Events tracked:
  - weekend_start: When the first session of a race weekend is < 24 h away
  - fp1/fp2/fp3_result: After each practice session ends (via OpenF1)
  - qualifying_result: When Jolpica has qualifying data for the latest round
  - pre_race: When the race is < 30 min away (pushes starting grid)
  - race_result: When Jolpica has race result data for the latest round
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING

from astrbot.api import logger

from . import _f1_api as api
from . import _formatter as fmt
from ._models import Success, Failure, JolpicaRace, JolpicaSessionSchedule

if TYPE_CHECKING:
    from astrbot.core.star.context import Context
    from astrbot.api.star import Star

POLL_INTERVAL = 60          # seconds
WEEKEND_START_THRESHOLD = timedelta(hours=24)  # notify 24 h before first session
PRE_RACE_THRESHOLD = timedelta(minutes=30)     # notify 30 min before race

_DEFAULT_STATE = {"last_notified_round": 0, "notified_events": []}


class F1Scheduler:
    """Manages automated F1 push notifications."""

    def __init__(self, star: "Star", context: "Context") -> None:
        self.ctx = context
        self._star = star
        self._subscribers: list[str] = []
        self._state: dict = dict(_DEFAULT_STATE)
        self._task: asyncio.Task | None = None
        self._loaded = False

    # ──────────────── public interface ────────────────

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())
            logger.info("[F1Notifier] Scheduler started.")

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            logger.info("[F1Notifier] Scheduler stopped.")

    async def add_subscriber(self, session: str) -> bool:
        if session not in self._subscribers:
            self._subscribers.append(session)
            await self._persist_subscribers()
            return True
        return False

    async def remove_subscriber(self, session: str) -> bool:
        if session in self._subscribers:
            self._subscribers.remove(session)
            await self._persist_subscribers()
            return True
        return False

    def has_subscriber(self, session: str) -> bool:
        return session in self._subscribers

    def subscriber_count(self) -> int:
        return len(self._subscribers)

    # ──────────────── KV persistence ────────────────

    async def _load(self) -> None:
        """Load subscribers and state from AstrBot KV store."""
        self._subscribers = await self._star.get_kv_data("f1_subscribers", []) or []
        self._state = await self._star.get_kv_data("f1_state", dict(_DEFAULT_STATE)) or dict(_DEFAULT_STATE)
        self._loaded = True
        logger.info(
            f"[F1Notifier] Loaded {len(self._subscribers)} subscriber(s) from KV store."
        )

    async def _persist_subscribers(self) -> None:
        await self._star.put_kv_data("f1_subscribers", self._subscribers)

    async def _persist_state(self) -> None:
        await self._star.put_kv_data("f1_state", self._state)

    # ──────────────── helpers ────────────────

    def _notified(self, round_num: int, event: str) -> bool:
        r = self._state.get("last_notified_round", 0)
        events = self._state.get("notified_events", [])
        return r == round_num and event in events

    async def _mark_notified(self, round_num: int, event: str) -> None:
        if self._state.get("last_notified_round") != round_num:
            self._state["last_notified_round"] = round_num
            self._state["notified_events"] = []
        if event not in self._state["notified_events"]:
            self._state["notified_events"].append(event)
        await self._persist_state()

    async def _broadcast(self, text: str) -> None:
        """Send message to all subscribers concurrently."""
        from astrbot.core.message.message_event_result import MessageChain
        from astrbot.api.message_components import Plain

        if not self._subscribers:
            return
        chain = MessageChain([Plain(text)])

        async def _send(session_str: str) -> None:
            try:
                ok = await self.ctx.send_message(session_str, chain)
                if not ok:
                    logger.warning(f"[F1Notifier] Failed to send to {session_str}")
            except Exception as e:
                logger.error(f"[F1Notifier] Broadcast error to {session_str}: {e}")

        await asyncio.gather(*[_send(s) for s in list(self._subscribers)])

    @staticmethod
    def _parse_utc(date_str: str, time_str: str) -> datetime:
        return datetime.strptime(
            f"{date_str}T{time_str.rstrip('Z')}", "%Y-%m-%dT%H:%M:%S"
        ).replace(tzinfo=timezone.utc)

    @staticmethod
    def _next_race(races: list[JolpicaRace]) -> JolpicaRace | None:
        now = datetime.now(timezone.utc)
        for race in races:
            try:
                if F1Scheduler._parse_utc(race.date, race.time) >= now:
                    return race
            except Exception:
                continue
        return None

    @staticmethod
    def _first_session_time(race: JolpicaRace) -> datetime | None:
        """Find the earliest session start time in the weekend."""
        slots: list[JolpicaSessionSchedule | None] = [
            race.first_practice,
            race.sprint_qualifying,
            race.second_practice,
            race.sprint,
            race.third_practice,
            race.qualifying,
        ]
        times: list[datetime] = []
        for slot in slots:
            if slot is not None:
                try:
                    times.append(F1Scheduler._parse_utc(slot.date, slot.time))
                except Exception:
                    continue
        return min(times) if times else None

    # ──────────────── main loop ────────────────

    async def _run(self) -> None:
        await self._load()
        while True:
            try:
                await self._check_and_notify()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[F1Notifier] Scheduler error: {e}", exc_info=True)
            await asyncio.sleep(POLL_INTERVAL)

    async def _check_and_notify(self) -> None:
        if not self._subscribers:
            return

        schedule_result = await api.get_current_schedule()
        match schedule_result:
            case Failure(error=err):
                logger.warning(f"[F1Notifier] Schedule fetch failed: {err}")
                return
            case Success(value=races):
                pass

        if not races:
            return

        now = datetime.now(timezone.utc)
        next_race = self._next_race(races)

        if next_race is not None:
            round_num = next_race.round_int
            race_time = self._parse_utc(next_race.date, next_race.time)

            # ── 1. Weekend start notification ──────────────────────────────
            first_session = self._first_session_time(next_race)
            if first_session is not None:
                delta = first_session - now
                if timedelta(0) <= delta <= WEEKEND_START_THRESHOLD:
                    if not self._notified(round_num, "weekend_start"):
                        msg = fmt.format_weekend_start(next_race)
                        await self._broadcast(msg)
                        await self._mark_notified(round_num, "weekend_start")
                        logger.info(f"[F1Notifier] Sent weekend_start for round {round_num}")

            # ── 2. Practice session result push ───────────────────────────
            fp_sessions = [
                (next_race.first_practice,  "1", "fp1_result"),
                (next_race.second_practice, "2", "fp2_result"),
                (next_race.third_practice,  "3", "fp3_result"),
            ]
            for fp_slot, fp_num, event_key in fp_sessions:
                if fp_slot is None:
                    continue
                fp_time = self._parse_utc(fp_slot.date, fp_slot.time)
                if now > fp_time + timedelta(minutes=90):
                    if not self._notified(round_num, event_key):
                        session_res = await api.get_practice_session(fp_num)
                        match session_res:
                            case Success(value=of1_session):
                                sk = of1_session.session_key
                                results_res = await api.get_session_result(sk)
                                drivers_res = await api.get_drivers_for_session(sk)
                                match (results_res, drivers_res):
                                    case (Success(value=results), Success(value=drivers_list)) if results:
                                        drivers_by_num = {d.driver_number: d for d in drivers_list}
                                        msg = fmt.format_practice_result(
                                            of1_session, results, drivers_by_num, fp_num
                                        )
                                        await self._broadcast(msg)
                                        await self._mark_notified(round_num, event_key)
                                        logger.info(f"[F1Notifier] Sent {event_key} for round {round_num}")
                                    case _:
                                        logger.debug(f"[F1Notifier] FP{fp_num} results not ready yet")
                            case Failure(error=err):
                                logger.warning(f"[F1Notifier] FP{fp_num} session not found: {err}")

            # ── 3. Qualifying result push ──────────────────────────────────
            if next_race.qualifying is not None:
                qual_time = self._parse_utc(next_race.qualifying.date, next_race.qualifying.time)
                if now > qual_time + timedelta(hours=2):
                    if not self._notified(round_num, "qualifying_result"):
                        qual_res = await api.get_qualifying_result(round_num)
                        match qual_res:
                            case Success(value=race) if race.qualifying_results:
                                msg = fmt.format_qualifying_result(race)
                                await self._broadcast(msg)
                                await self._mark_notified(round_num, "qualifying_result")
                                logger.info(f"[F1Notifier] Sent qualifying_result for round {round_num}")
                            case Failure(error=err):
                                logger.warning(f"[F1Notifier] Qualifying result not ready: {err}")

            # ── 4. Pre-race starting grid push ────────────────────────────
            delta_race = race_time - now
            if timedelta(0) <= delta_race <= PRE_RACE_THRESHOLD:
                if not self._notified(round_num, "pre_race"):
                    session_res = await api.get_latest_session("Race")
                    match session_res:
                        case Success(value=session):
                            sk = session.session_key
                            drivers_res = await api.get_drivers_for_session(sk)
                            grid_res = await api.get_starting_grid(sk)
                            match (drivers_res, grid_res):
                                case (Success(value=drivers_list), Success(value=grid)) if grid:
                                    drivers_by_num = {d.driver_number: d for d in drivers_list}
                                    msg = fmt.format_starting_grid(drivers_by_num, grid)
                                case _:
                                    msg = fmt.format_next_race(next_race) + "\n\n🏁 正赛即将开始！"
                        case Failure():
                            msg = fmt.format_next_race(next_race) + "\n\n🏁 正赛即将开始！"
                    await self._broadcast(msg)
                    await self._mark_notified(round_num, "pre_race")
                    logger.info(f"[F1Notifier] Sent pre_race for round {round_num}")

        # ── 5. Race result push (most recently finished race) ──────────────
        finished = [
            r for r in races
            if (
                (dt := fmt.race_utc(r)) is not None
                and dt + timedelta(hours=3) < now
            )
        ]
        if finished:
            latest_finished = finished[-1]
            lf_round = latest_finished.round_int
            if not self._notified(lf_round, "race_result"):
                race_res = await api.get_race_result(lf_round)
                match race_res:
                    case Success(value=race) if race.race_results:
                        msg = fmt.format_race_result(race)
                        await self._broadcast(msg)
                        await self._mark_notified(lf_round, "race_result")
                        logger.info(f"[F1Notifier] Sent race_result for round {lf_round}")
                    case Failure(error=err):
                        logger.warning(f"[F1Notifier] Race result not ready: {err}")

            # ── 6. Sprint result push ──────────────────────────────────────
            if latest_finished.sprint is not None:
                sprint_time = self._parse_utc(
                    latest_finished.sprint.date, latest_finished.sprint.time
                )
                if now > sprint_time + timedelta(hours=2):
                    if not self._notified(lf_round, "sprint_result"):
                        sprint_res = await api.get_sprint_result(lf_round)
                        match sprint_res:
                            case Success(value=race) if race.sprint_results:
                                msg = fmt.format_sprint_result(race)
                                await self._broadcast(msg)
                                await self._mark_notified(lf_round, "sprint_result")
                                logger.info(f"[F1Notifier] Sent sprint_result for round {lf_round}")
                            case Failure(error=err):
                                logger.warning(f"[F1Notifier] Sprint result not ready: {err}")
