"""Pydantic models for F1 API responses.

Design mirrors the Rust pattern:
  - serde_json  →  Pydantic model_validate()
  - Result<T,E> →  ApiResult = Success[T] | Failure
  - Option<T>   →  T | None with None as default

All models use ``extra="ignore"`` so unknown API fields are silently dropped.

Data sources:
  - OpenF1 (https://api.openf1.org/v1/): schedule, results, practice, grid
  - Jolpica-F1 (https://api.jolpi.ca/ergast/f1/): standings only
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Generic, TypeVar

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
ApiResult = Success[T] | Failure


# ── Shared config ──────────────────────────────────────────────────────────────

_CFG = ConfigDict(extra="ignore", populate_by_name=True)


# ── F1 core models (populated from OpenF1) ─────────────────────────────────────


class F1SessionSlot(BaseModel):
    """A weekend sub-session time slot (FP1, Qualifying, Sprint, etc.)."""

    model_config = _CFG

    date: str = ""   # "YYYY-MM-DD"
    time: str = ""   # "HH:MM:SSZ"


class F1RaceResult(BaseModel):
    """Single driver result from a race session."""

    model_config = _CFG

    position: int = 99
    driver_name: str = ""
    driver_first_name: str = ""
    driver_last_name: str = ""
    team_name: str = ""
    time: str | None = None     # "1:23:45.678", "+1.234", "+1 LAP" etc.
    laps: str = ""
    points: str = "0"
    status: str = ""            # "Finished" / "DNF" / "DNS" / "DSQ"


class F1QualifyingResult(BaseModel):
    """Single driver result from a qualifying session."""

    model_config = _CFG

    position: int = 99
    driver_name: str = ""
    driver_first_name: str = ""
    driver_last_name: str = ""
    team_name: str = ""
    q1: str = "—"
    q2: str = "—"
    q3: str = "—"

    @field_validator("q1", "q2", "q3", mode="before")
    @classmethod
    def _default_dash(cls, v: object) -> str:
        return str(v) if v else "—"


class F1SprintResult(BaseModel):
    """Single driver result from a sprint session."""

    model_config = _CFG

    position: int = 99
    driver_name: str = ""
    driver_first_name: str = ""
    driver_last_name: str = ""
    team_name: str = ""
    time: str | None = None
    laps: str = ""
    points: str = "0"
    status: str = ""


class F1RaceWeekend(BaseModel):
    """Full race-weekend entry built from OpenF1 meetings + sessions data."""

    model_config = _CFG

    season: str = ""
    round: str = "0"
    race_name: str = ""
    circuit_id: str = ""          # mapped from circuit_short_name for image lookup
    circuit_name: str = ""        # circuit_short_name from OpenF1
    locality: str = ""            # e.g. "Marina Bay"
    country: str = ""             # e.g. "Singapore"
    country_code: str = ""        # e.g. "SGP"
    meeting_key: int = 0
    date: str = ""                # race date "YYYY-MM-DD"
    time: str = ""                # race time "HH:MM:SSZ"

    # Optional sub-sessions
    first_practice: F1SessionSlot | None = None
    second_practice: F1SessionSlot | None = None
    third_practice: F1SessionSlot | None = None
    qualifying: F1SessionSlot | None = None
    sprint: F1SessionSlot | None = None
    sprint_qualifying: F1SessionSlot | None = None

    # Results (populated only when fetched via result endpoints)
    race_results: list[F1RaceResult] = Field(default_factory=list)
    qualifying_results: list[F1QualifyingResult] = Field(default_factory=list)
    sprint_results: list[F1SprintResult] = Field(default_factory=list)

    @property
    def round_int(self) -> int:
        try:
            return int(self.round)
        except ValueError:
            return 0

    @property
    def is_sprint_weekend(self) -> bool:
        return self.sprint is not None


# ── Jolpica-F1 models (standings only) ─────────────────────────────────────────


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
    meeting_key: int = 0


class OpenF1Driver(BaseModel):
    model_config = _CFG

    driver_number: int = 0
    full_name: str | None = None
    last_name: str | None = None
    name_acronym: str | None = None
    team_name: str | None = None
    headshot_url: str | None = None
    team_colour: str | None = None

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
    duration: float | None = None  # best lap in seconds
    gap_to_leader: float | None = None
    number_of_laps: int | None = None


class OpenF1Meeting(BaseModel):
    model_config = _CFG

    meeting_key: int = 0
    meeting_name: str = ""
    meeting_official_name: str = ""
    country_name: str = ""
    country_code: str = ""
    circuit_short_name: str = ""
    circuit_key: int = 0
    location: str = ""
    date_start: str = ""
    date_end: str = ""
    year: int = 0
    gmt_offset: str = ""


# ── Convenient type aliases (TYPE_CHECKING only — not evaluated at runtime) ────
#
# ``ApiResult = Union[Success[T], Failure]`` cannot be subscripted at runtime
# because Union does not create a generic class.  These names are only used as
# string annotations (PEP 563 / ``from __future__ import annotations``), so they
# must be imported inside ``if TYPE_CHECKING`` blocks in other modules.

if TYPE_CHECKING:
    from typing import TypeAlias

    ScheduleResult: TypeAlias = Success[list[F1RaceWeekend]] | Failure
    RaceResult: TypeAlias = Success[F1RaceWeekend] | Failure
    StandingsResult: TypeAlias = Success[list[JolpicaDriverStanding]] | Failure
    ConstructorStandingsResult: TypeAlias = (
        Success[list[JolpicaConstructorStanding]] | Failure
    )
    SessionResult: TypeAlias = Success[OpenF1Session] | Failure
    SessionResultsResult: TypeAlias = Success[list[OpenF1SessionResult]] | Failure
    DriversResult: TypeAlias = Success[list[OpenF1Driver]] | Failure
    GridResult: TypeAlias = Success[list[OpenF1Position]] | Failure
    MeetingResult: TypeAlias = Success[OpenF1Meeting] | Failure
