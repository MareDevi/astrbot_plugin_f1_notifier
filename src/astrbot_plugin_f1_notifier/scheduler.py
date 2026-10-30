"""Persistent, recipient-aware delivery for F1 weekend notifications."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from copy import deepcopy
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from .api import F1Client, RateLimitError
from .models import Race, Table, parse_datetime

logger = logging.getLogger(__name__)
STATE_KEY = "f1_v2_state"
RESULT_DURATIONS = {
    "practice1": 60,
    "practice2": 60,
    "practice3": 60,
    "sprint_qualifying": 60,
    "qualifying": 90,
    "sprint": 90,
    "race": 180,
}


class F1Scheduler:
    """Load state before accepting subscriptions and checkpoint each recipient.

    Args:
        client: Plugin-owned data client.
        load: AstrBot KV reader.
        save: AstrBot KV writer.
        send: Deliver a table to one origin and report actual success.
        config: Validated plugin configuration.
    """

    def __init__(
        self,
        client: F1Client,
        load: Callable[..., Awaitable],
        save: Callable[..., Awaitable],
        send: Callable[[str, Table], Awaitable[bool]],
        config: dict,
    ) -> None:
        self.client = client
        self._load = load
        self._save = save
        self._send = send
        self.config = config
        self._state: dict = {"version": 2, "subscribers": {}, "delivered": {}}
        self._lock = asyncio.Lock()
        self._tick_lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._ready = False

    @property
    def subscriber_count(self) -> int:
        """Return the number of loaded subscriber origins."""
        return len(self._state["subscribers"])

    async def initialize(self, now: datetime | None = None) -> None:
        """Restore persisted state, migrating legacy subscriber origins once.

        Args:
            now: Reference UTC time, defaulting to the current clock.

        Raises:
            ValueError: If saved state is malformed; existing data is preserved.
        """
        now = now or datetime.now(timezone.utc)
        async with self._lock:
            stored = await self._load(STATE_KEY, None)
            if stored is None:
                origins = await self._load("f1_subscribers", []) or []
                if not isinstance(origins, list) or any(
                    not isinstance(s, str) or not s for s in origins
                ):
                    raise ValueError("Invalid legacy subscriber data")
                stored = {
                    "version": 2,
                    "subscribers": {
                        s: (now - timedelta(hours=48)).isoformat() for s in origins
                    },
                    "delivered": {},
                }
                # Legacy delivery IDs had no season or recipient information.
                # Migrating them could suppress a different season's results.
                await self._save(STATE_KEY, stored)
            if (
                not isinstance(stored, dict)
                or stored.get("version") != 2
                or not isinstance(stored.get("subscribers"), dict)
                or not isinstance(stored.get("delivered"), dict)
            ):
                raise ValueError("Invalid F1 delivery state")
            for origin, since in stored["subscribers"].items():
                if (
                    not isinstance(origin, str)
                    or not origin
                    or not isinstance(since, str)
                ):
                    raise ValueError("Invalid subscriber entry")
                parse_datetime(since)
            for entry in stored["delivered"].values():
                if (
                    not isinstance(entry, dict)
                    or not isinstance(entry.get("recipients"), list)
                    or any(not isinstance(s, str) for s in entry["recipients"])
                ):
                    raise ValueError("Invalid delivery entry")
                parse_datetime(entry["expires"])
            self._state = deepcopy(stored)
            self._ready = True

    async def subscribe(self, origin: str, now: datetime | None = None) -> bool:
        """Persist a new subscription before making it visible in memory.

        Args:
            origin: AstrBot unified message origin.
            now: Reference time for excluding historical result replays.

        Returns:
            True if the subscription was newly created.

        Raises:
            RuntimeError: If initialization has not completed.
        """
        if not self._ready:
            raise RuntimeError("Scheduler is not initialized")
        async with self._lock:
            if origin in self._state["subscribers"]:
                return False
            state = deepcopy(self._state)
            state["subscribers"][origin] = (
                now or datetime.now(timezone.utc)
            ).isoformat()
            await self._save(STATE_KEY, state)
            self._state = state
            return True

    async def unsubscribe(self, origin: str) -> bool:
        """Remove one origin only after its persisted state is updated.

        Args:
            origin: AstrBot unified message origin.

        Returns:
            True if an existing subscription was removed.
        """
        async with self._lock:
            if origin not in self._state["subscribers"]:
                return False
            state = deepcopy(self._state)
            del state["subscribers"][origin]
            await self._save(STATE_KEY, state)
            self._state = state
            return True

    def start(self) -> None:
        """Start a single polling task after state restoration.

        Raises:
            RuntimeError: If initialization has not completed.
        """
        if not self._ready:
            raise RuntimeError("Scheduler is not initialized")
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="f1-notifier")

    async def stop(self) -> None:
        """Cancel and await polling before the plugin releases its clients."""
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        """Keep provider and delivery failures isolated from the polling loop."""
        while True:
            try:
                await self.tick()
            except Exception:
                logger.exception("F1 notification poll failed; retrying next interval")
            await asyncio.sleep(60)

    async def tick(self, now: datetime | None = None) -> None:
        """Check both upcoming reminders and recently completed weekends.

        Args:
            now: Reference clock for deterministic event-window evaluation.
        """
        if not self._ready or not self.subscriber_count or self._tick_lock.locked():
            return
        now = now or datetime.now(timezone.utc)
        async with self._tick_lock:
            seasons = {
                now.year,
                (now - timedelta(hours=48)).year,
                (now + timedelta(days=7)).year,
            }
            races = []
            for season in sorted(seasons):
                try:
                    races.extend(await self.client.schedule(season))
                except RateLimitError:
                    continue
                except Exception:
                    logger.exception("F1 schedule unavailable for season %s", season)
            async with self._lock:
                state = deepcopy(self._state)
                state["delivered"] = {
                    k: v
                    for k, v in state["delivered"].items()
                    if parse_datetime(v["expires"]) >= now
                }
                if state != self._state:
                    await self._save(STATE_KEY, state)
                    self._state = state
            for race in races:
                for kind, due, expires, session_start in self._events(race):
                    if not due <= now < expires:
                        continue
                    event = f"{race.key}:{kind}"
                    sent = self._state["delivered"].get(event, {}).get("recipients", [])
                    recipients = [
                        s
                        for s, since in self._state["subscribers"].items()
                        if s not in sent
                        and (
                            kind in ("weekend", "grid")
                            or parse_datetime(since) <= session_start
                        )
                    ]
                    if not recipients:
                        continue
                    try:
                        if kind == "weekend":
                            from .formatter import weekend_table

                            table = weekend_table(
                                race, self.config.get("timezone", "Asia/Shanghai")
                            )
                            table = Table(
                                "比赛周开始提醒",
                                table.subtitle,
                                table.columns,
                                table.rows,
                                table.footer,
                                table.circuit_id,
                            )
                        else:
                            table = await self.client.results(race, kind)
                        if table is not None:
                            await self._deliver(event, expires, recipients, table)
                    except RateLimitError:
                        continue
                    except Exception:
                        logger.exception(
                            "F1 event %s failed; retaining pending recipients", event
                        )

    def _events(self, race: Race) -> list[tuple[str, datetime, datetime, datetime]]:
        """Derive bounded reminder and result windows from the full weekend.

        Args:
            race: Weekend with known session start times.

        Returns:
            Event kind, eligible time, expiry, and source session start tuples.
        """
        first = min(s.start for s in race.sessions)
        local_first = first.astimezone(
            ZoneInfo(self.config.get("timezone", "Asia/Shanghai"))
        )
        # Anchor to the first session's local week, even if the race ends on Monday.
        reminder_date = local_first.date() + timedelta(
            days=("周一", "周二", "周三").index(
                self.config.get("weekend_push_day", "周一")
            )
            - local_first.weekday()
        )
        reminder = datetime.combine(
            reminder_date,
            time.fromisoformat(self.config.get("weekend_push_time", "09:00")),
            tzinfo=local_first.tzinfo,
        ).astimezone(timezone.utc)
        events = [
            ("weekend", reminder, first, first),
            ("grid", race.start - timedelta(minutes=30), race.start, race.start),
        ]
        delay = timedelta(minutes=self.config.get("result_poll_delay", 5))
        for session in race.sessions:
            end = session.end or session.start + timedelta(
                minutes=RESULT_DURATIONS[session.key]
            )
            events.append(
                (session.key, end + delay, end + timedelta(hours=48), session.start)
            )
        return events

    async def _deliver(
        self, event: str, expires: datetime, recipients: list[str], table: Table
    ) -> None:
        """Checkpoint successful recipients while leaving failures retryable.

        Args:
            event: Season, round, and notification identity.
            expires: Time after which a delayed notification is no longer useful.
            recipients: Subscriber origins still awaiting this event.
            table: Notification content, shared by text and image output.
        """
        for origin in recipients:
            if origin not in self._state["subscribers"]:
                continue
            try:
                if not await self._send(origin, table):
                    logger.warning("F1 delivery rejected for %s", origin)
                    continue
            except Exception:
                logger.exception("F1 delivery failed for %s", origin)
                continue
            async with self._lock:
                state = deepcopy(self._state)
                entry = state["delivered"].setdefault(
                    event, {"expires": expires.isoformat(), "recipients": []}
                )
                if origin not in entry["recipients"]:
                    entry["recipients"].append(origin)
                await self._save(STATE_KEY, state)
                self._state = state
