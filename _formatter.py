"""Message formatter module for F1 notifications.

All functions return a plain-text string ready to send as a chat message.
Times are converted from UTC to Asia/Shanghai (UTC+8) for display.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any

CST = timezone(timedelta(hours=8))  # UTC+8

# ─────────────────────── helpers ───────────────────────

FLAG_MAP: dict[str, str] = {
    "Australia": "🇦🇺", "China": "🇨🇳", "Japan": "🇯🇵", "Bahrain": "🇧🇭",
    "Saudi Arabia": "🇸🇦", "USA": "🇺🇸", "United States": "🇺🇸", "Canada": "🇨🇦",
    "Monaco": "🇲🇨", "Spain": "🇪🇸", "Austria": "🇦🇹", "UK": "🇬🇧",
    "United Kingdom": "🇬🇧", "Belgium": "🇧🇪", "Hungary": "🇭🇺",
    "Netherlands": "🇳🇱", "Italy": "🇮🇹", "Azerbaijan": "🇦🇿",
    "Singapore": "🇸🇬", "Mexico": "🇲🇽", "Brazil": "🇧🇷",
    "UAE": "🇦🇪", "United Arab Emirates": "🇦🇪", "Qatar": "🇶🇦",
}

POSITION_MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}


def _flag(country: str) -> str:
    return FLAG_MAP.get(country, "🏁")


def _utc_to_cst(date_str: str, time_str: str) -> str:
    """Convert 'YYYY-MM-DD' + 'HH:MM:SSZ' to 'MM-DD HH:MM CST'."""
    try:
        dt_utc = datetime.strptime(
            f"{date_str}T{time_str.rstrip('Z')}", "%Y-%m-%dT%H:%M:%S"
        ).replace(tzinfo=timezone.utc)
        dt_cst = dt_utc.astimezone(CST)
        return dt_cst.strftime("%m-%d %H:%M")
    except Exception:
        return f"{date_str} {time_str}"


def _session_time(race: dict, session: str) -> str | None:
    """Get formatted time for a race weekend session."""
    s = race.get(session)
    if not s:
        return None
    return _utc_to_cst(s["date"], s["time"])


# ─────────────────────── public formatters ───────────────────────


def format_schedule(races: list[dict], limit: int = 5) -> str:
    """Format upcoming race schedule."""
    now = datetime.now(timezone.utc)
    upcoming = [
        r for r in races
        if datetime.fromisoformat(f"{r['date']}T{r['time'].rstrip('Z')}+00:00") >= now
    ][:limit]

    if not upcoming:
        return "📅 本赛季剩余赛程已全部完成，期待下赛季！"

    lines = [f"📅 F1 {upcoming[0]['season']} 赛季 · 近期赛程\n"]
    for i, r in enumerate(upcoming, 1):
        flag = _flag(r["Circuit"]["Location"]["country"])
        race_time = _utc_to_cst(r["date"], r["time"])
        sprint_tag = " 🏃 冲刺赛周末" if r.get("Sprint") else ""
        lines.append(
            f"  第{r['round']}站{sprint_tag}\n"
            f"  {flag} {r['raceName']}\n"
            f"  🏎 正赛: {race_time} (CST)\n"
        )
    return "\n".join(lines)


def format_next_race(race: dict) -> str:
    """Format full weekend timetable for the next race."""
    flag = _flag(race["Circuit"]["Location"]["country"])
    circuit = race["Circuit"]["circuitName"]
    locality = race["Circuit"]["Location"]["locality"]
    country = race["Circuit"]["Location"]["country"]

    session_map = [
        ("FirstPractice",    "FP1"),
        ("SprintQualifying", "冲刺排位"),
        ("SecondPractice",   "FP2"),
        ("Sprint",           "冲刺赛"),
        ("ThirdPractice",    "FP3"),
        ("Qualifying",       "排位赛"),
    ]

    lines = [
        f"🏎 第{race['round']}站 — {flag} {race['raceName']}",
        f"📍 {circuit}, {locality}, {country}",
        "",
        "🗓 赛程安排 (北京时间 CST):",
    ]
    for key, label in session_map:
        t = _session_time(race, key)
        if t:
            lines.append(f"  {label}: {t}")

    race_time = _utc_to_cst(race["date"], race["time"])
    lines.append(f"  ✅ 正赛: {race_time}")
    return "\n".join(lines)


def format_race_result(race: dict) -> str:
    """Format full race result."""
    flag = _flag(race["Circuit"]["Location"]["country"])
    lines = [
        f"🏁 正赛结果 — {flag} {race['raceName']} (第{race['round']}站)",
        "",
    ]
    for res in race.get("Results", []):
        pos = int(res["position"])
        medal = POSITION_MEDALS.get(pos, f" {pos:>2}.")
        drv = res["Driver"]
        name = f"{drv['givenName']} {drv['familyName']}"
        team = res["Constructor"]["name"]
        status = res.get("status", "")
        time_val = res.get("Time", {}).get("time", status)
        laps = res.get("laps", "?")
        pts = res.get("points", "0")
        lines.append(
            f"{medal} {name} ({team})\n"
            f"       ⏱ {time_val}  圈数: {laps}  积分: {pts}"
        )
    return "\n".join(lines)


def format_qualifying_result(race: dict) -> str:
    """Format qualifying result."""
    flag = _flag(race["Circuit"]["Location"]["country"])
    lines = [
        f"⏱ 排位赛结果 — {flag} {race['raceName']} (第{race['round']}站)",
        "",
    ]
    for res in race.get("QualifyingResults", []):
        pos = int(res["position"])
        medal = POSITION_MEDALS.get(pos, f" {pos:>2}.")
        drv = res["Driver"]
        name = f"{drv['givenName']} {drv['familyName']}"
        team = res["Constructor"]["name"]
        q1 = res.get("Q1", "-")
        q2 = res.get("Q2", "-")
        q3 = res.get("Q3", "-")
        lines.append(
            f"{medal} {name} ({team})\n"
            f"       Q1:{q1}  Q2:{q2}  Q3:{q3}"
        )
    return "\n".join(lines)


def format_sprint_result(race: dict) -> str:
    """Format sprint race result."""
    flag = _flag(race["Circuit"]["Location"]["country"])
    lines = [
        f"🏃 冲刺赛结果 — {flag} {race['raceName']} (第{race['round']}站)",
        "",
    ]
    for res in race.get("SprintResults", []):
        pos = int(res["position"])
        medal = POSITION_MEDALS.get(pos, f" {pos:>2}.")
        drv = res["Driver"]
        name = f"{drv['givenName']} {drv['familyName']}"
        team = res["Constructor"]["name"]
        status = res.get("status", "")
        time_val = res.get("Time", {}).get("time", status)
        pts = res.get("points", "0")
        lines.append(
            f"{medal} {name} ({team})\n"
            f"       ⏱ {time_val}  积分: {pts}"
        )
    return "\n".join(lines)


def format_driver_standings(standings: list[dict], limit: int = 10) -> str:
    """Format driver championship standings."""
    lines = ["🏆 车手积分榜\n"]
    for entry in standings[:limit]:
        pos = int(entry["position"])
        medal = POSITION_MEDALS.get(pos, f" {pos:>2}.")
        drv = entry["Driver"]
        name = f"{drv['givenName']} {drv['familyName']}"
        team = entry["Constructors"][0]["name"] if entry.get("Constructors") else "?"
        pts = entry["points"]
        wins = entry.get("wins", 0)
        lines.append(f"{medal} {name} ({team})  {pts}分  🏆{wins}胜")
    return "\n".join(lines)


def format_constructor_standings(standings: list[dict]) -> str:
    """Format constructor championship standings."""
    lines = ["🏗 车队积分榜\n"]
    for entry in standings:
        pos = int(entry["position"])
        medal = POSITION_MEDALS.get(pos, f" {pos:>2}.")
        name = entry["Constructor"]["name"]
        pts = entry["points"]
        wins = entry.get("wins", 0)
        lines.append(f"{medal} {name}  {pts}分  🏆{wins}胜")
    return "\n".join(lines)


def format_starting_grid(drivers_by_number: dict[int, dict], grid: list[dict]) -> str:
    """Format OpenF1 starting grid.

    drivers_by_number: {driver_number: driver_dict from OpenF1}
    grid: list of position dict sorted by position (from get_starting_grid)
    """
    lines = ["🏁 发车顺序\n"]
    for entry in grid:
        pos = entry.get("position", "?")
        drv_num = entry.get("driver_number")
        drv = drivers_by_number.get(drv_num, {})
        name = drv.get("full_name") or drv.get("last_name", f"#{drv_num}")
        team = drv.get("team_name", "")
        medal = POSITION_MEDALS.get(int(pos) if str(pos).isdigit() else 99, f" {pos:>2}.")
        lines.append(f"{medal} {name} ({team})")
    return "\n".join(lines)


def format_weekend_start(race: dict) -> str:
    """Notification sent when a race weekend is about to begin."""
    return (
        f"🏎 F1 赛车周末即将开始！\n\n"
        + format_next_race(race)
        + "\n\n加油！🏁"
    )


def _format_lap_duration(seconds: float) -> str:
    """Convert float seconds to 'm:ss.sss' lap time string."""
    if seconds <= 0:
        return "-"
    m = int(seconds) // 60
    s = seconds - m * 60
    return f"{m}:{s:06.3f}"


def format_practice_result(
    session: dict,
    results: list[dict],
    drivers_by_number: dict[int, dict],
    fp_number: str = "1",
) -> str:
    """Format OpenF1 practice session result.

    session: OpenF1 session dict
    results: output of get_session_result(), sorted by position
    drivers_by_number: {driver_number: OpenF1 driver dict}
    fp_number: '1', '2', or '3'
    """
    # OpenF1 session has: circuit_short_name, location, country_name (no meeting_name)
    circuit = session.get("circuit_short_name") or session.get("location", "")
    country = session.get("country_name", "")
    flag = FLAG_MAP.get(country, "🏁")

    lines = [
        f"🔧 FP{fp_number} 练习赛结果 — {flag} {circuit}",
        "",
    ]

    if not results:
        lines.append("暂无练习赛结果数据，请练习赛结束后再试。")
        return "\n".join(lines)

    for entry in results:
        pos = entry.get("position", "?")
        drv_num = entry.get("driver_number")
        drv = drivers_by_number.get(drv_num, {})
        name = drv.get("full_name") or drv.get("last_name", f"#{drv_num}")
        team = drv.get("team_name", "")
        duration = entry.get("duration")
        lap_time = _format_lap_duration(duration) if duration else "-"
        gap = entry.get("gap_to_leader")
        gap_str = "" if not gap else f"  +{gap:.3f}s"
        medal = POSITION_MEDALS.get(int(pos) if str(pos).isdigit() else 99, f" {pos:>2}.")
        lines.append(f"{medal} {name} ({team})\n       ⏱ {lap_time}{gap_str}")

    return "\n".join(lines)
