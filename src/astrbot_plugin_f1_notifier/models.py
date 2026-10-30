"""Small immutable models shared by data access and presentation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

SESSION_NAMES = {
    "practice1": "第一次练习赛",
    "practice2": "第二次练习赛",
    "practice3": "第三次练习赛",
    "sprint_qualifying": "冲刺排位赛",
    "qualifying": "排位赛",
    "sprint": "冲刺赛",
    "race": "正赛",
}


def parse_datetime(value: str) -> datetime:
    """Parse an explicit timestamp without guessing a missing timezone.

    Args:
        value: ISO 8601 timestamp with an offset or Z suffix.

    Returns:
        A timezone-aware UTC timestamp.

    Raises:
        ValueError: If the timestamp is invalid or has no timezone.
    """
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Timestamp must include a timezone")
    return result.astimezone(timezone.utc)


@dataclass(frozen=True)
class Session:
    """A scheduled session with an optional confirmed end time."""

    key: str
    start: datetime
    end: datetime | None = None


@dataclass(frozen=True)
class Race:
    """One Grand Prix weekend, identified by season and round."""

    season: int
    round: int
    name: str
    circuit_id: str
    circuit_name: str
    country: str
    sessions: tuple[Session, ...]

    @property
    def start(self) -> datetime:
        """Return the scheduled race start."""
        return next(session.start for session in self.sessions if session.key == "race")

    @property
    def key(self) -> str:
        """Return a stable identity that cannot collide across seasons."""
        return f"{self.season}:{self.round}"

    @classmethod
    def from_jolpica(cls, raw: dict) -> Race:
        """Normalize a Jolpica weekend without inventing unknown start times.

        Args:
            raw: A race entry from the Jolpica RaceTable.

        Returns:
            A weekend with known sessions sorted by start time.

        Raises:
            ValueError: If the season, round, or race start is invalid.
            KeyError: If required race fields are absent.
        """
        sessions = [Session("race", parse_datetime(f"{raw['date']}T{raw['time']}"))]
        for field, key in (
            ("FirstPractice", "practice1"),
            ("SecondPractice", "practice2"),
            ("ThirdPractice", "practice3"),
            ("SprintQualifying", "sprint_qualifying"),
            ("SprintShootout", "sprint_qualifying"),
            ("Qualifying", "qualifying"),
            ("Sprint", "sprint"),
        ):
            slot = raw.get(field) or {}
            if (
                slot.get("date")
                and slot.get("time")
                and not any(s.key == key for s in sessions)
            ):
                sessions.append(
                    Session(key, parse_datetime(f"{slot['date']}T{slot['time']}"))
                )
        circuit = raw.get("Circuit") or {}
        return cls(
            int(raw["season"]),
            int(raw["round"]),
            raw.get("raceName", "Grand Prix"),
            circuit.get("circuitId", ""),
            circuit.get("circuitName", ""),
            (circuit.get("Location") or {}).get("country", ""),
            tuple(sorted(sessions, key=lambda s: s.start)),
        )


@dataclass(frozen=True)
class Table:
    """A source-neutral table consumed by text and image output."""

    title: str
    subtitle: str = ""
    columns: tuple[str, ...] = ()
    rows: tuple[tuple[str, ...], ...] = ()
    footer: str = ""
    circuit_id: str = ""
