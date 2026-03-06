"""Pydantic models for F1 API responses.

Design mirrors the Rust pattern:
  - serde_json  →  Pydantic model_validate()
  - Result<T,E> →  ApiResult = Union[Success[T], Failure]
  - Option<T>   →  Optional[T] with None as default

All models use ``extra="ignore"`` so unknown API fields are silently dropped.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Generic, Optional, TypeVar, Union
from pydantic import BaseModel, ConfigDict, Field, field_validator


# ── ApiResult ─────────────────────────────────────────────────────────────────

T = TypeVar("T")


class Success(BaseModel, Generic[T]):
    """Mirrors Rust ``Ok(T)``."""

    ok: bool = True
    value: T


class Failure(BaseModel):
    """Mirrors Rust ``Err(E)``."""

    ok: bool = False
    error: str


# ``ApiResult[T]`` is the return type of every public API function.
# Callers use ``match result: case Success(value=...): case Failure(error=...):``
ApiResult = Union[Success[T], Failure]


# ── Shared config ──────────────────────────────────────────────────────────────

_CFG = ConfigDict(extra="ignore", populate_by_name=True)


# ── Jolpica-F1 models ──────────────────────────────────────────────────────────


class JolpicaDriver(BaseModel):
    model_config = _CFG

    driver_id: str = Field("", alias="driverId")
    given_name: str = Field("", alias="givenName")
    family_name: str = Field("", alias="familyName")
    nationality: str = ""

    @property
    def full_name(self) -> str:
        return f"{self.given_name} {self.family_name}".strip()


class JolpicaConstructor(BaseModel):
    model_config = _CFG

    constructor_id: str = Field("", alias="constructorId")
    name: str = ""
    nationality: str = ""


class JolpicaTime(BaseModel):
    """Generic time entry used in RaceResult / SprintResult."""

    model_config = _CFG

    millis: Optional[str] = None
    time: Optional[str] = None


class JolpicaFastestLap(BaseModel):
    model_config = _CFG

    rank: str = ""
    lap: str = ""
    time: Optional[JolpicaTime] = None


class JolpicaRaceResult(BaseModel):
    model_config = _CFG

    number: str = ""
    position: str = "0"
    position_text: str = Field("", alias="positionText")
    points: str = "0"
    driver: JolpicaDriver = Field(default_factory=JolpicaDriver, alias="Driver")
    constructor: JolpicaConstructor = Field(
        default_factory=JolpicaConstructor, alias="Constructor"
    )
    grid: str = ""
    laps: str = ""
    status: str = ""
    time: Optional[JolpicaTime] = Field(None, alias="Time")
    fastest_lap: Optional[JolpicaFastestLap] = Field(None, alias="FastestLap")

    @property
    def pos_int(self) -> int:
        try:
            return int(self.position)
        except ValueError:
            return 99


class JolpicaQualifyingResult(BaseModel):
    model_config = _CFG

    number: str = ""
    position: str = "0"
    driver: JolpicaDriver = Field(default_factory=JolpicaDriver, alias="Driver")
    constructor: JolpicaConstructor = Field(
        default_factory=JolpicaConstructor, alias="Constructor"
    )
    q1: str = Field("—", alias="Q1")
    q2: str = Field("—", alias="Q2")
    q3: str = Field("—", alias="Q3")

    @field_validator("q1", "q2", "q3", mode="before")
    @classmethod
    def _default_dash(cls, v: object) -> str:
        return str(v) if v else "—"

    @property
    def pos_int(self) -> int:
        try:
            return int(self.position)
        except ValueError:
            return 99


class JolpicaSprintResult(BaseModel):
    model_config = _CFG

    number: str = ""
    position: str = "0"
    position_text: str = Field("", alias="positionText")
    points: str = "0"
    driver: JolpicaDriver = Field(default_factory=JolpicaDriver, alias="Driver")
    constructor: JolpicaConstructor = Field(
        default_factory=JolpicaConstructor, alias="Constructor"
    )
    grid: str = ""
    laps: str = ""
    status: str = ""
    time: Optional[JolpicaTime] = Field(None, alias="Time")

    @property
    def pos_int(self) -> int:
        try:
            return int(self.position)
        except ValueError:
            return 99


class JolpicaSessionSchedule(BaseModel):
    """A weekend sub-session slot (FP1, Qualifying, Sprint, etc.)."""

    model_config = _CFG

    date: str = ""
    time: str = ""


class JolpicaCircuitLocation(BaseModel):
    model_config = _CFG

    locality: str = ""
    country: str = ""


class JolpicaCircuit(BaseModel):
    model_config = _CFG

    circuit_id: str = Field("", alias="circuitId")
    circuit_name: str = Field("", alias="circuitName")
    location: JolpicaCircuitLocation = Field(
        default_factory=JolpicaCircuitLocation, alias="Location"
    )


class JolpicaRace(BaseModel):
    """Full race-weekend entry from the Jolpica schedule / results endpoints."""

    model_config = _CFG

    season: str = ""
    round: str = "0"
    race_name: str = Field("", alias="raceName")
    circuit: JolpicaCircuit = Field(default_factory=JolpicaCircuit, alias="Circuit")
    date: str = ""
    time: str = ""

    # Optional sub-sessions (only present when the round has them)
    first_practice: Optional[JolpicaSessionSchedule] = Field(
        None, alias="FirstPractice"
    )
    second_practice: Optional[JolpicaSessionSchedule] = Field(
        None, alias="SecondPractice"
    )
    third_practice: Optional[JolpicaSessionSchedule] = Field(
        None, alias="ThirdPractice"
    )
    sprint_qualifying: Optional[JolpicaSessionSchedule] = Field(
        None, alias="SprintQualifying"
    )
    sprint: Optional[JolpicaSessionSchedule] = Field(None, alias="Sprint")
    qualifying: Optional[JolpicaSessionSchedule] = Field(None, alias="Qualifying")

    # Results (only present when fetched via results / qualifying / sprint endpoints)
    race_results: list[JolpicaRaceResult] = Field([], alias="Results")
    qualifying_results: list[JolpicaQualifyingResult] = Field(
        [], alias="QualifyingResults"
    )
    sprint_results: list[JolpicaSprintResult] = Field([], alias="SprintResults")

    @property
    def round_int(self) -> int:
        try:
            return int(self.round)
        except ValueError:
            return 0

    @property
    def is_sprint_weekend(self) -> bool:
        return self.sprint is not None


class JolpicaDriverStanding(BaseModel):
    model_config = _CFG

    position: str = "0"
    points: str = "0"
    wins: str = "0"
    driver: JolpicaDriver = Field(default_factory=JolpicaDriver, alias="Driver")
    constructors: list[JolpicaConstructor] = Field([], alias="Constructors")

    @property
    def pos_int(self) -> int:
        try:
            return int(self.position)
        except ValueError:
            return 99

    @property
    def primary_team(self) -> str:
        return self.constructors[0].name if self.constructors else "?"


class JolpicaConstructorStanding(BaseModel):
    model_config = _CFG

    position: str = "0"
    points: str = "0"
    wins: str = "0"
    constructor: JolpicaConstructor = Field(
        default_factory=JolpicaConstructor, alias="Constructor"
    )

    @property
    def pos_int(self) -> int:
        try:
            return int(self.position)
        except ValueError:
            return 99


# ── OpenF1 models ──────────────────────────────────────────────────────────────


class OpenF1Session(BaseModel):
    model_config = _CFG

    session_key: int = 0
    session_name: str = ""
    date_start: str = ""
    circuit_short_name: str = ""
    country_name: str = ""
    location: str = ""
    year: int = 0


class OpenF1Driver(BaseModel):
    model_config = _CFG

    driver_number: int = 0
    full_name: Optional[str] = None
    last_name: Optional[str] = None
    name_acronym: Optional[str] = None
    team_name: Optional[str] = None

    @property
    def display_name(self) -> str:
        return self.full_name or self.last_name or f"#{self.driver_number}"


class OpenF1Position(BaseModel):
    model_config = _CFG

    driver_number: int = 0
    position: int = 99
    date: str = ""


class OpenF1SessionResult(BaseModel):
    model_config = _CFG

    position: int = 99
    driver_number: int = 0
    duration: Optional[float] = None       # best lap in seconds
    gap_to_leader: Optional[float] = None
    number_of_laps: Optional[int] = None


class OpenF1Meeting(BaseModel):
    model_config = _CFG

    meeting_key: int = 0
    meeting_name: str = ""
    country_name: str = ""
    circuit_short_name: str = ""


# ── Convenient type aliases (TYPE_CHECKING only — not evaluated at runtime) ────
#
# ``ApiResult = Union[Success[T], Failure]`` cannot be subscripted at runtime
# because Union does not create a generic class.  These names are only used as
# string annotations (PEP 563 / ``from __future__ import annotations``), so they
# must be imported inside ``if TYPE_CHECKING`` blocks in other modules.

if TYPE_CHECKING:
    from typing import TypeAlias
    ScheduleResult: TypeAlias = Union[Success[list[JolpicaRace]], Failure]
    RaceResult: TypeAlias = Union[Success[JolpicaRace], Failure]
    StandingsResult: TypeAlias = Union[Success[list[JolpicaDriverStanding]], Failure]
    ConstructorStandingsResult: TypeAlias = Union[Success[list[JolpicaConstructorStanding]], Failure]
    SessionResult: TypeAlias = Union[Success[OpenF1Session], Failure]
    SessionResultsResult: TypeAlias = Union[Success[list[OpenF1SessionResult]], Failure]
    DriversResult: TypeAlias = Union[Success[list[OpenF1Driver]], Failure]
    GridResult: TypeAlias = Union[Success[list[OpenF1Position]], Failure]
    MeetingResult: TypeAlias = Union[Success[OpenF1Meeting], Failure]
