"""Chinese presentation helpers for F1 messages and cards."""

from __future__ import annotations

from zoneinfo import ZoneInfo

from .models import Race, Table

SESSION_LABELS = {
    "practice1": "练习赛 FP1",
    "practice2": "练习赛 FP2",
    "practice3": "练习赛 FP3",
    "sprint_qualifying": "冲刺排位赛",
    "sprint": "冲刺赛",
    "qualifying": "排位赛",
    "race": "正赛",
}


def format_table(table: Table) -> str:
    """Format a structured table as a Chinese chat message.

    Args:
        table: Structured presentation content.

    Returns:
        A plain-text message that keeps the table's reading order.
    """
    lines = [f"🏎 {table.title}"]
    if table.subtitle:
        lines.append(table.subtitle)

    if table.columns:
        lines.extend(("", "  |  ".join(table.columns)))
    if table.rows:
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.extend("  |  ".join(row) for row in table.rows)

    if table.footer:
        lines.extend(("━━━━━━━━━━━━━━━━━━━━", table.footer))
    return "\n".join(lines)


def schedule_table(races: list[Race], timezone: str = "Asia/Shanghai") -> Table:
    """Build a race schedule table with every available session.

    Args:
        races: Formula 1 races to include.
        timezone: IANA timezone used for every displayed timestamp.

    Returns:
        A table with races and sessions sorted by start time.

    Raises:
        ZoneInfoNotFoundError: If the timezone name is unknown.
    """
    zone = ZoneInfo(timezone)
    rows: list[tuple[str, ...]] = []
    session_count = 0
    seasons = sorted({race.season for race in races})

    for race in sorted(
        races,
        key=lambda item: item.start,
    ):
        sessions = sorted(race.sessions, key=lambda session: session.start)
        for session in sessions:
            start = session.start.astimezone(zone)
            offset = start.strftime("%z")
            offset_label = f"UTC{offset[:3]}:{offset[3:]}"
            rows.append(
                (
                    start.strftime("%Y-%m-%d %H:%M"),
                    f"第{race.round}站 · {race.name}",
                    SESSION_LABELS.get(session.key, session.key),
                    offset_label,
                )
            )
            session_count += 1

    season_label = "、".join(str(season) for season in seasons) or "当前"
    return Table(
        title=f"F1 {season_label} 赛季赛程",
        subtitle=f"时区：{timezone}",
        columns=("开始时间", "分站", "赛段", "UTC 偏移"),
        rows=tuple(rows),
        footer=f"共 {len(races)} 场比赛 · {session_count} 个赛段",
    )


def weekend_table(race: Race, timezone: str = "Asia/Shanghai") -> Table:
    """Build a complete race-weekend timetable.

    Args:
        race: Race whose sessions should be displayed.
        timezone: IANA timezone used for every displayed timestamp.

    Returns:
        A table with all sessions ordered from practice to race.

    Raises:
        ZoneInfoNotFoundError: If the timezone name is unknown.
    """
    zone = ZoneInfo(timezone)
    rows: list[tuple[str, ...]] = []
    sessions = sorted(race.sessions, key=lambda session: session.start)

    for session in sessions:
        start = session.start.astimezone(zone)
        offset = start.strftime("%z")
        offset_label = f"UTC{offset[:3]}:{offset[3:]}"
        if session.end is None:
            end_label = "—"
        else:
            end = session.end.astimezone(zone)
            end_label = end.strftime("%Y-%m-%d %H:%M")
        rows.append(
            (
                start.strftime("%Y-%m-%d %H:%M"),
                SESSION_LABELS.get(session.key, session.key),
                end_label,
                offset_label,
            )
        )

    return Table(
        title=f"F1 第{race.round}站 · {race.name}",
        subtitle=f"{race.country} · {race.circuit_name} · 时区：{timezone}",
        columns=("开始时间", "赛段", "结束时间", "UTC 偏移"),
        rows=tuple(rows),
        footer="赛程可能调整，请以赛事官方公告为准",
        circuit_id=race.circuit_id,
    )
