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
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from astrbot.api import logger

from . import api
from . import formatter as fmt
from . import image_renderer as img
from .models import Failure, JolpicaRace, JolpicaSessionSchedule, OpenF1Session, Success

if TYPE_CHECKING:
    from astrbot.api import AstrBotConfig
    from astrbot.api.star import Star
    from astrbot.core.star.context import Context

POLL_INTERVAL = 60  # seconds
MIN_ERROR_SLEEP = 10  # minimum sleep after an error (anti-avalanche)
BROADCAST_CONCURRENCY = 5  # max concurrent sends in _broadcast
WEEKEND_START_THRESHOLD = timedelta(hours=24)  # notify 24 h before first session
PRE_RACE_THRESHOLD = timedelta(minutes=30)  # notify 30 min before race


def _default_state() -> dict:
    """Factory function to create a fresh default state dict."""
    return {"last_notified_round": 0, "notified_events": []}


class F1Scheduler:
    """Manages automated F1 push notifications."""

    def __init__(
        self, star: Star, context: Context, config: AstrBotConfig | None = None
    ) -> None:
        self.ctx = context
        self._star = star
        self._config = config
        self._subscribers: list[str] = []
        self._state: dict = _default_state()
        self._task: asyncio.Task | None = None
        self._loaded = False

    @property
    def _image_mode(self) -> bool:
        return (
            bool(self._config.get("enable_image_render", False))
            if self._config
            else False
        )

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
        self._state = (
            await self._star.get_kv_data("f1_state", _default_state())
            or _default_state()
        )
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

    async def _broadcast(self, text: str, image_path: str | None = None) -> None:
        """Send message to all subscribers with limited concurrency.

        If image mode is enabled and image_path is provided, sends the
        image. Falls back to plain text on failure.
        """
        from astrbot.api.message_components import Image, Plain
        from astrbot.core.message.message_event_result import MessageChain

        if not self._subscribers:
            return

        # Try image if enabled
        chain = None
        if self._image_mode and image_path is not None:
            try:
                chain = MessageChain([Image.fromFileSystem(image_path)])
            except Exception as e:
                logger.warning(
                    f"[F1Notifier] Broadcast image failed, fallback to text: {e}"
                )

        if chain is None:
            chain = MessageChain([Plain(text)])

        sem = asyncio.Semaphore(BROADCAST_CONCURRENCY)

        async def _send(session_str: str) -> None:
            async with sem:
                try:
                    ok = await self.ctx.send_message(session_str, chain)
                    if not ok:
                        logger.warning(f"[F1Notifier] Failed to send to {session_str}")
                except Exception as e:
                    logger.error(f"[F1Notifier] Broadcast error to {session_str}: {e}")

        await asyncio.gather(*[_send(s) for s in self._subscribers.copy()])

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
            except ValueError:
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
                except ValueError:
                    continue
        return min(times) if times else None

    @staticmethod
    def _session_matches_slot(session: OpenF1Session, expected_time: datetime) -> bool:
        """Check that the OpenF1 session started within 24 h of *expected_time*.

        This guards against the API returning a stale session from a
        previous race weekend when data for the current weekend is delayed.
        """
        try:
            session_start = datetime.fromisoformat(
                session.date_start.replace("Z", "+00:00")
            )
        except (ValueError, AttributeError):
            return False
        return abs(session_start - expected_time) < timedelta(hours=24)

    # ──────────────── main loop ────────────────

    async def _run(self) -> None:
        await self._load()
        loop = asyncio.get_running_loop()
        while True:
            start = loop.time()
            try:
                await self._check_and_notify()
                elapsed = loop.time() - start
                sleep_time = max(MIN_ERROR_SLEEP, POLL_INTERVAL - elapsed)
                await asyncio.sleep(sleep_time)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[F1Notifier] Scheduler error: {e}", exc_info=True)
                elapsed = loop.time() - start
                sleep_time = max(MIN_ERROR_SLEEP, POLL_INTERVAL - elapsed)
                try:
                    await asyncio.sleep(sleep_time)
                except asyncio.CancelledError:
                    break

    async def _check_and_notify(self) -> None:
        if not self._subscribers:
            return

        schedule_result = await api.get_current_schedule()
        match schedule_result:
            case Failure(error=err):
                logger.warning(f"[F1Notifier] Schedule fetch failed: {err}")
                return
            case Success(value=races):
                if not races:
                    return
                now = datetime.now(timezone.utc)
                next_race = self._next_race(races)
                if next_race is not None:
                    round_num = next_race.round_int
                    race_time = self._parse_utc(next_race.date, next_race.time)
                    await self._check_weekend_start(next_race, round_num, now)
                    await self._check_practice_sessions(next_race, round_num, now)
                    await self._check_qualifying(next_race, round_num, now)
                    await self._check_pre_race(next_race, round_num, now, race_time)
                await self._check_race_results(races, now)

    async def _check_weekend_start(
        self, next_race: JolpicaRace, round_num: int, now: datetime
    ) -> None:
        """Notify subscribers when the first session of the weekend is < 24 h away."""
        first_session = self._first_session_time(next_race)
        if first_session is None:
            return
        delta = first_session - now
        if timedelta(0) <= delta <= WEEKEND_START_THRESHOLD:
            if not self._notified(round_num, "weekend_start"):
                msg = fmt.format_weekend_start(next_race)
                image_path = img.render_weekend_start(next_race)
                await self._broadcast(msg, image_path)
                await self._mark_notified(round_num, "weekend_start")
                logger.info(f"[F1Notifier] Sent weekend_start for round {round_num}")

    async def _check_practice_sessions(
        self, next_race: JolpicaRace, round_num: int, now: datetime
    ) -> None:
        """Push practice session results once each session has been over for 90 min."""
        fp_sessions = [
            (next_race.first_practice, "1", "fp1_result"),
            (next_race.second_practice, "2", "fp2_result"),
            (next_race.third_practice, "3", "fp3_result"),
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
                            # Validate session belongs to current race weekend
                            # by checking date_start is within 24 h of the
                            # expected practice time (robust against country
                            # name mismatches across APIs).
                            if not self._session_matches_slot(of1_session, fp_time):
                                logger.debug(
                                    f"[F1Notifier] FP{fp_num} session "
                                    f"date_start='{of1_session.date_start}' "
                                    f"is not within 24 h of expected slot, "
                                    f"skipping"
                                )
                                continue
                            sk = of1_session.session_key
                            results_res, drivers_res = await asyncio.gather(
                                api.get_session_result(sk),
                                api.get_drivers_for_session(sk),
                            )
                            match (results_res, drivers_res):
                                case (
                                    Success(value=results),
                                    Success(value=drivers_list),
                                ) if results:
                                    drivers_by_num = {
                                        d.driver_number: d for d in drivers_list
                                    }
                                    msg = fmt.format_practice_result(
                                        of1_session, results, drivers_by_num, fp_num
                                    )
                                    image_path = img.render_practice_result(
                                        of1_session,
                                        results,
                                        drivers_by_num,
                                        fp_num,
                                        circuit_id=next_race.circuit.circuit_id,
                                    )
                                    await self._broadcast(msg, image_path)
                                    await self._mark_notified(round_num, event_key)
                                    logger.info(
                                        f"[F1Notifier] Sent {event_key} for round {round_num}"
                                    )
                                case (Failure(error=err), _):
                                    logger.warning(
                                        f"[F1Notifier] Failed to fetch FP{fp_num} results: {err}"
                                    )
                                case (_, Failure(error=err)):
                                    logger.warning(
                                        f"[F1Notifier] Failed to fetch FP{fp_num} drivers: {err}"
                                    )
                                case _:
                                    logger.debug(
                                        f"[F1Notifier] FP{fp_num} results not ready yet"
                                    )
                        case Failure(error=err):
                            logger.warning(
                                f"[F1Notifier] FP{fp_num} session not found: {err}"
                            )

    async def _check_qualifying(
        self, next_race: JolpicaRace, round_num: int, now: datetime
    ) -> None:
        """Push qualifying results once qualifying has been over for 2 hours."""
        if next_race.qualifying is None:
            return
        qual_time = self._parse_utc(
            next_race.qualifying.date, next_race.qualifying.time
        )
        if now > qual_time + timedelta(hours=2):
            if not self._notified(round_num, "qualifying_result"):
                qual_res = await api.get_qualifying_result(round_num)
                match qual_res:
                    case Success(value=race) if race.qualifying_results:
                        msg = fmt.format_qualifying_result(race)
                        image_path = img.render_qualifying_result(race)
                        await self._broadcast(msg, image_path)
                        await self._mark_notified(round_num, "qualifying_result")
                        logger.info(
                            f"[F1Notifier] Sent qualifying_result for round {round_num}"
                        )
                    case Failure(error=err):
                        logger.warning(
                            f"[F1Notifier] Qualifying result not ready: {err}"
                        )

    async def _check_pre_race(
        self,
        next_race: JolpicaRace,
        round_num: int,
        now: datetime,
        race_time: datetime,
    ) -> None:
        """Push the starting grid when the race is within 30 minutes of starting."""
        delta_race = race_time - now
        if not (timedelta(0) <= delta_race <= PRE_RACE_THRESHOLD):
            return
        if self._notified(round_num, "pre_race"):
            return

        session_res = await api.get_latest_session("Race")
        image_path: str | None = None
        match session_res:
            case Success(value=session):
                sk = session.session_key
                drivers_res, grid_res = await asyncio.gather(
                    api.get_drivers_for_session(sk),
                    api.get_starting_grid(sk),
                )
                match (drivers_res, grid_res):
                    case (Success(value=drivers_list), Success(value=grid)) if grid:
                        drivers_by_num = {d.driver_number: d for d in drivers_list}
                        msg = fmt.format_starting_grid(drivers_by_num, grid)
                        image_path = img.render_starting_grid(
                            drivers_by_num,
                            grid,
                            circuit_id=next_race.circuit.circuit_id,
                        )
                    case _:
                        msg = fmt.format_next_race(next_race) + "\n\n🏁 正赛即将开始！"
                        image_path = img.render_next_race(next_race)
            case Failure():
                msg = fmt.format_next_race(next_race) + "\n\n🏁 正赛即将开始！"
                image_path = img.render_next_race(next_race)

        await self._broadcast(msg, image_path)
        await self._mark_notified(round_num, "pre_race")
        logger.info(f"[F1Notifier] Sent pre_race for round {round_num}")

    async def _check_race_results(
        self, races: list[JolpicaRace], now: datetime
    ) -> None:
        """Push race and sprint results for the most recently finished race."""
        finished = [
            r
            for r in races
            if ((dt := fmt.race_utc(r)) is not None and dt + timedelta(hours=3) < now)
        ]
        if not finished:
            return

        latest_finished = finished[-1]
        lf_round = latest_finished.round_int

        if not self._notified(lf_round, "race_result"):
            race_res = await api.get_race_result(lf_round)
            match race_res:
                case Success(value=race) if race.race_results:
                    msg = fmt.format_race_result(race)
                    image_path = img.render_race_result(race)
                    await self._broadcast(msg, image_path)
                    await self._mark_notified(lf_round, "race_result")
                    logger.info(f"[F1Notifier] Sent race_result for round {lf_round}")
                case Failure(error=err):
                    logger.warning(f"[F1Notifier] Race result not ready: {err}")

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
                            image_path = img.render_sprint_result(race)
                            await self._broadcast(msg, image_path)
                            await self._mark_notified(lf_round, "sprint_result")
                            logger.info(
                                f"[F1Notifier] Sent sprint_result for round {lf_round}"
                            )
                        case Failure(error=err):
                            logger.warning(
                                f"[F1Notifier] Sprint result not ready: {err}"
                            )
