"""Message formatter module for F1 notifications.

All functions accept typed Pydantic models from ``models.py`` and return
plain-text strings ready to send as chat messages.
Times are converted from UTC to Asia/Shanghai (UTC+8) for display.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .models import (
    F1RaceWeekend,
    F1SessionSlot,
    JolpicaConstructorStanding,
    JolpicaDriverStanding,
    OpenF1Driver,
    OpenF1Position,
    OpenF1Session,
    OpenF1SessionResult,
)

CST = timezone(timedelta(hours=8))  # UTC+8

# ─────────────────────── helpers ───────────────────────

FLAG_MAP: dict[str, str] = {
    "Australia": "🇦🇺",
    "China": "🇨🇳",
    "Japan": "🇯🇵",
    "Bahrain": "🇧🇭",
    "Saudi Arabia": "🇸🇦",
    "USA": "🇺🇸",
    "United States": "🇺🇸",
    "Canada": "🇨🇦",
    "Monaco": "🇲🇨",
    "Spain": "🇪🇸",
    "Austria": "🇦🇹",
    "UK": "🇬🇧",
    "United Kingdom": "🇬🇧",
    "Belgium": "🇧🇪",
    "Hungary": "🇭🇺",
    "Netherlands": "🇳🇱",
    "Italy": "🇮🇹",
    "Azerbaijan": "🇦🇿",
    "Singapore": "🇸🇬",
    "Mexico": "🇲🇽",
    "Brazil": "🇧🇷",
    "UAE": "🇦🇪",
    "United Arab Emirates": "🇦🇪",
    "Qatar": "🇶🇦",
}

POSITION_MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}


def _flag(country: str) -> str:
    return FLAG_MAP.get(country, "🏁")


def _medal(pos: int) -> str:
    return POSITION_MEDALS.get(pos, f" {pos:>2}.")


def _utc_to_cst(date_str: str, time_str: str) -> str:
    """Convert 'YYYY-MM-DD' + 'HH:MM:SSZ' to 'MM-DD HH:MM CST'."""
    try:
        return datetime.fromisoformat(f"{date_str}T{time_str}").astimezone(CST).strftime("%m-%d %H:%M")
    except (ValueError, TypeError, AttributeError):
        return f"{date_str} {time_str}"


def _session_cst(s: F1SessionSlot | None) -> str | None:
    """Return formatted CST time for an optional weekend session slot."""
    if s is None:
        return None
    return _utc_to_cst(s.date, s.time)


def _format_lap_duration(seconds: float) -> str:
    """Convert float seconds to 'm:ss.sss' lap-time string."""
    if seconds <= 0:
        return "-"
    m = int(seconds) // 60
    s = seconds - m * 60
    return f"{m}:{s:06.3f}"


def race_utc(race: F1RaceWeekend) -> datetime | None:
    try:
        return datetime.fromisoformat(f"{race.date}T{race.time}")
    except (ValueError, TypeError, AttributeError):
        return None


# ─────────────────────── public formatters ───────────────────────


def format_schedule(races: list[F1RaceWeekend], limit: int = 5) -> str:
    """Format upcoming race schedule with all session times."""
    now = datetime.now(timezone.utc)
    upcoming: list[F1RaceWeekend] = []
    for race in races:
        dt = race_utc(race)
        if dt is not None and dt >= now:
            upcoming.append(race)
    upcoming = upcoming[:limit]

    if not upcoming:
        return "📅 本赛季剩余赛程已全部完成，期待下赛季！"

    lines = [f"📅 F1 {upcoming[0].season} 赛季 · 近期赛程\n"]
    for race in upcoming:
        flag = _flag(race.country)
        sprint_tag = " 🏃 冲刺赛周末" if race.is_sprint_weekend else ""
        lines.append(f"第{race.round}站{sprint_tag}\n{flag} {race.race_name}")

        session_slots: list[tuple[F1SessionSlot | None, str]] = [
            (race.first_practice, "FP1 练习赛"),
            (race.sprint_qualifying, "冲刺排位"),
            (race.second_practice, "FP2 练习赛"),
            (race.sprint, "冲刺赛"),
            (race.third_practice, "FP3 练习赛"),
            (race.qualifying, "排位赛"),
        ]
        for slot, label in session_slots:
            t = _session_cst(slot)
            if t:
                lines.append(f"  {label}: {t}")

        race_time = _utc_to_cst(race.date, race.time)
        lines.append(f"  ✅ 正赛: {race_time} (CST)\n")
    return "\n".join(lines)


def format_next_race(race: F1RaceWeekend) -> str:
    """Format full weekend timetable for the next race."""
    country = race.country
    flag = _flag(country)
    circuit = race.circuit_name
    locality = race.locality

    session_slots: list[tuple[F1SessionSlot | None, str]] = [
        (race.first_practice, "FP1"),
        (race.sprint_qualifying, "冲刺排位"),
        (race.second_practice, "FP2"),
        (race.sprint, "冲刺赛"),
        (race.third_practice, "FP3"),
        (race.qualifying, "排位赛"),
    ]

    lines = [
        f"🏎 第{race.round}站 — {flag} {race.race_name}",
        f"📍 {circuit}, {locality}, {country}",
        "",
        "🗓 赛程安排 (北京时间 CST):",
    ]
    for slot, label in session_slots:
        t = _session_cst(slot)
        if t:
            lines.append(f"  {label}: {t}")

    race_time = _utc_to_cst(race.date, race.time)
    lines.append(f"  ✅ 正赛: {race_time}")
    return "\n".join(lines)


def format_race_result(race: F1RaceWeekend) -> str:
    """Format full race result."""
    flag = _flag(race.country)
    lines = [
        f"🏁 正赛结果 — {flag} {race.race_name} (第{race.round}站)",
        "",
    ]
    for res in race.race_results:
        pos = res.position
        time_val = res.time if res.time else res.status
        lines.append(
            f"{_medal(pos)} {res.driver_name} ({res.team_name})\n"
            f"       ⏱ {time_val}  圈数: {res.laps}  积分: {res.points}"
        )
    return "\n".join(lines)


def format_qualifying_result(race: F1RaceWeekend) -> str:
    """Format qualifying result."""
    flag = _flag(race.country)
    lines = [
        f"⏱ 排位赛结果 — {flag} {race.race_name} (第{race.round}站)",
        "",
    ]
    for res in race.qualifying_results:
        pos = res.position
        lines.append(
            f"{_medal(pos)} {res.driver_name} ({res.team_name})\n"
            f"       Q1:{res.q1}  Q2:{res.q2}  Q3:{res.q3}"
        )
    return "\n".join(lines)


def format_sprint_result(race: F1RaceWeekend) -> str:
    """Format sprint race result."""
    flag = _flag(race.country)
    lines = [
        f"🏃 冲刺赛结果 — {flag} {race.race_name} (第{race.round}站)",
        "",
    ]
    for res in race.sprint_results:
        pos = res.position
        time_val = res.time if res.time else res.status
        lines.append(
            f"{_medal(pos)} {res.driver_name} ({res.team_name})\n"
            f"       ⏱ {time_val}  积分: {res.points}"
        )
    return "\n".join(lines)


def format_driver_standings(
    standings: list[JolpicaDriverStanding], limit: int = 10
) -> str:
    """Format driver championship standings."""
    lines = ["🏆 车手积分榜\n"]
    for entry in standings[:limit]:
        pos = entry.pos_int
        lines.append(
            f"{_medal(pos)} {entry.driver.full_name} ({entry.primary_team})"
            f"  {entry.points}分  🏆{entry.wins}胜"
        )
    return "\n".join(lines)


def format_constructor_standings(
    standings: list[JolpicaConstructorStanding],
) -> str:
    """Format constructor championship standings."""
    lines = ["🏗 车队积分榜\n"]
    for entry in standings:
        pos = entry.pos_int
        lines.append(
            f"{_medal(pos)} {entry.constructor.name}"
            f"  {entry.points}分  🏆{entry.wins}胜"
        )
    return "\n".join(lines)


def format_starting_grid(
    drivers_by_number: dict[int, OpenF1Driver],
    grid: list[OpenF1Position],
) -> str:
    """Format OpenF1 starting grid."""
    lines = ["🏁 发车顺序\n"]
    for entry in grid:
        pos = entry.position
        drv = drivers_by_number.get(
            entry.driver_number, OpenF1Driver(driver_number=entry.driver_number)
        )
        medal = _medal(pos)
        lines.append(f"{medal} {drv.display_name} ({drv.team_name or ''})")
    return "\n".join(lines)


def format_weekend_start(race: F1RaceWeekend) -> str:
    """Notification sent when a race weekend is about to begin."""
    return "🏎 F1 赛车周末即将开始！\n\n" + format_next_race(race) + "\n\n加油！🏁"


def format_practice_result(
    session: OpenF1Session,
    results: list[OpenF1SessionResult],
    drivers_by_number: dict[int, OpenF1Driver],
    fp_number: str = "1",
) -> str:
    """Format OpenF1 practice session result."""
    circuit = session.circuit_short_name or session.location
    flag = _flag(session.country_name)

    lines = [
        f"🔧 FP{fp_number} 练习赛结果 — {flag} {circuit}",
        "",
    ]

    if not results:
        lines.append("暂无练习赛结果数据，请练习赛结束后再试。")
        return "\n".join(lines)

    for entry in results:
        pos = entry.position
        drv = drivers_by_number.get(
            entry.driver_number, OpenF1Driver(driver_number=entry.driver_number)
        )
        lap_time = _format_lap_duration(entry.duration) if entry.duration else "-"
        match entry.gap_to_leader:
            case float(g) | int(g) if g > 0:
                gap_str = f"  +{g:.3f}s"
            case _:
                gap_str = ""
        lines.append(
            f"{_medal(pos)} {drv.display_name} ({drv.team_name or ''})\n"
            f"       ⏱ {lap_time}{gap_str}"
        )

    return "\n".join(lines)
