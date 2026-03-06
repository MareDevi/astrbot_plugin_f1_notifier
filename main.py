"""AstrBot plugin: F1 Notifier

Pushes F1 race notifications automatically and provides on-demand
query commands for race information.

Commands (prefix /f1):
  /f1 schedule           — upcoming race schedule
  /f1 next               — next race full timetable
  /f1 result [round]     — race result (default: latest)
  /f1 qualifying [round] — qualifying result (default: latest)
  /f1 sprint [round]     — sprint race result
  /f1 standings drivers  — driver championship standings
  /f1 standings teams    — constructor standings
  /f1 subscribe          — subscribe to auto notifications
  /f1 unsubscribe        — unsubscribe from notifications
"""

from __future__ import annotations

from datetime import datetime, timezone

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger

from . import _f1_api as api
from . import _formatter as fmt
from ._scheduler import F1Scheduler

HELP_TEXT = """🏎 F1 Notifier 指令列表

/f1 schedule           — 近期赛程（最近5场）
/f1 next               — 下一站完整时间表
/f1 result [round]     — 正赛结果（默认最近一场）
/f1 qualifying [round] — 排位赛结果
/f1 sprint [round]     — 冲刺赛结果
/f1 practice [1|2|3]   — 练习赛最快圈速（默认FP1）
/f1 standings drivers  — 车手积分榜
/f1 standings teams    — 车队积分榜
/f1 subscribe          — 订阅自动推送
/f1 unsubscribe        — 取消订阅

自动推送内容：
  • 比赛周开始提醒（含完整赛程表）
  • 练习赛最快圈速（FP1/FP2/FP3）
  • 排位赛结果
  • 冲刺赛结果（冲刺周末）
  • 正赛发车前30分钟（含发车顺序）
  • 正赛结果
"""


@register(
    "astrbot_plugin_f1_notifier",
    "MareDevi",
    "F1赛事推送与查询插件",
    "1.0.0",
    "https://github.com/MareDevi/astrbot_plugin_f1_notifier",
)
class F1NotifierPlugin(Star):
    def __init__(self, context: Context) -> None:
        super().__init__(context)
        self.scheduler = F1Scheduler(self, context)

    async def initialize(self) -> None:
        """Start the background notification scheduler."""
        self.scheduler.start()
        logger.info("[F1Notifier] Plugin initialized.")

    async def terminate(self) -> None:
        """Stop the scheduler and close HTTP session."""
        await self.scheduler.stop()
        await api.close_session()
        logger.info("[F1Notifier] Plugin terminated.")

    # ──────────────────── command_group ────────────────────────────
    @filter.command_group("f1")
    def f1(self) -> None:
        """F1赛事查询与推送"""

    # ──────────────────── sub-commands ─────────────────────────────

    @f1.command("help")
    async def f1_help(self, event: AstrMessageEvent):
        """显示帮助信息"""
        yield event.plain_result(HELP_TEXT)

    @f1.command("schedule")
    async def f1_schedule(self, event: AstrMessageEvent):
        """查看近期赛程（最近5站）"""
        try:
            races = await api.get_current_schedule()
            yield event.plain_result(fmt.format_schedule(races))
        except Exception as e:
            logger.error(f"[F1Notifier] /f1 schedule error: {e}")
            yield event.plain_result("❌ 获取赛程失败，请稍后重试。")

    @f1.command("next")
    async def f1_next(self, event: AstrMessageEvent):
        """查看下一站完整时间表"""
        try:
            races = await api.get_current_schedule()
            now = datetime.now(timezone.utc)
            next_race = None
            for r in races:
                date_str = r.get("date")
                time_str = r.get("time")
                if not date_str or not time_str:
                    continue
                try:
                    race_dt = datetime.fromisoformat(
                        f"{date_str}T{time_str.rstrip('Z')}+00:00"
                    )
                except (ValueError, TypeError):
                    continue
                if race_dt >= now:
                    next_race = r
                    break
            if not next_race:
                yield event.plain_result("📅 本赛季剩余赛程已全部完成，期待下赛季！")
            else:
                yield event.plain_result(fmt.format_next_race(next_race))
        except Exception as e:
            logger.error(f"[F1Notifier] /f1 next error: {e}")
            yield event.plain_result("❌ 获取下一站信息失败，请稍后重试。")

    @f1.command("result")
    async def f1_result(self, event: AstrMessageEvent, round: str = "last"):
        """查看正赛结果，可指定站次 round"""
        round_arg: int | str = int(round) if round.isdigit() else "last"
        try:
            result = await api.get_race_result(round_arg)
            if not result or not result.get("Results"):
                yield event.plain_result("⏳ 正赛结果暂未公布，请比赛结束后再试。")
                return
            yield event.plain_result(fmt.format_race_result(result))
        except Exception as e:
            logger.error(f"[F1Notifier] /f1 result error: {e}")
            yield event.plain_result("❌ 获取比赛结果失败，请稍后重试。")

    @f1.command("qualifying")
    async def f1_qualifying(self, event: AstrMessageEvent, round: str = "last"):
        """查看排位赛结果，可指定站次 round"""
        round_arg: int | str = int(round) if round.isdigit() else "last"
        try:
            result = await api.get_qualifying_result(round_arg)
            if not result or not result.get("QualifyingResults"):
                yield event.plain_result("⏳ 排位赛结果暂未公布，请稍后再试。")
                return
            yield event.plain_result(fmt.format_qualifying_result(result))
        except Exception as e:
            logger.error(f"[F1Notifier] /f1 qualifying error: {e}")
            yield event.plain_result("❌ 获取排位赛结果失败，请稍后重试。")

    @f1.command("sprint")
    async def f1_sprint(self, event: AstrMessageEvent, round: str = "last"):
        """查看冲刺赛结果，可指定站次 round"""
        round_arg: int | str = int(round) if round.isdigit() else "last"
        try:
            result = await api.get_sprint_result(round_arg)
            if not result or not result.get("SprintResults"):
                yield event.plain_result("⏳ 冲刺赛结果暂未公布，或该站无冲刺赛。")
                return
            yield event.plain_result(fmt.format_sprint_result(result))
        except Exception as e:
            logger.error(f"[F1Notifier] /f1 sprint error: {e}")
            yield event.plain_result("❌ 获取冲刺赛结果失败，请稍后重试。")

    @f1.command("practice")
    async def f1_practice(self, event: AstrMessageEvent, session: str = "1"):
        """查看练习赛最快圈速。session: 1/2/3（默认1，也可输入 fp1/fp2/fp3）"""
        normalized = session.lower().removeprefix("fp") or "1"  # 'fp1' → '1', '1' → '1'
        if normalized not in ("1", "2", "3"):
            yield event.plain_result("❌ 请输入有效的练习赛场次：1、2、或 3（如 /f1 practice 2）")
            return
        try:
            of1_session = await api.get_practice_session(normalized)
            if not of1_session:
                yield event.plain_result(f"⏳ 暂未找到 FP{normalized} 练习赛数据，请练习赛结束后再试。")
                return
            sk = of1_session["session_key"]
            results = await api.get_session_result(sk)
            drivers_list = await api.get_drivers_for_session(sk)
            drivers_by_num = {d["driver_number"]: d for d in drivers_list}
            if not results:
                yield event.plain_result(f"⏳ FP{normalized} 结果数据暂未就绪，请练习赛结束后再试。")
                return
            yield event.plain_result(
                fmt.format_practice_result(of1_session, results, drivers_by_num, normalized)
            )
        except Exception as e:
            logger.error(f"[F1Notifier] /f1 practice error: {e}")
            yield event.plain_result("❌ 获取练习赛数据失败，请稍后重试。")

    @f1.command("standings")
    async def f1_standings(self, event: AstrMessageEvent, type: str = "drivers"):
        """查看积分榜。type: drivers（默认）或 teams"""
        try:
            if type.lower() in ("teams", "constructors", "team", "车队"):
                standings = await api.get_constructor_standings()
                yield event.plain_result(fmt.format_constructor_standings(standings))
            else:
                standings = await api.get_driver_standings()
                yield event.plain_result(fmt.format_driver_standings(standings))
        except Exception as e:
            logger.error(f"[F1Notifier] /f1 standings error: {e}")
            yield event.plain_result("❌ 获取积分榜失败，请稍后重试。")

    @f1.command("subscribe")
    async def f1_subscribe(self, event: AstrMessageEvent):
        """订阅当前会话的自动推送"""
        session = event.unified_msg_origin
        added = await self.scheduler.add_subscriber(session)
        if added:
            yield event.plain_result(
                f"✅ 已订阅 F1 自动推送！\n"
                f"当前共 {self.scheduler.subscriber_count()} 个会话已订阅。\n"
                "将自动推送：赛程提醒、排位/冲刺赛结果、发车顺序、正赛结果。"
            )
        else:
            yield event.plain_result("ℹ️ 当前会话已经订阅过了。发送 /f1 unsubscribe 可以取消。")

    @f1.command("unsubscribe")
    async def f1_unsubscribe(self, event: AstrMessageEvent):
        """取消当前会话的自动推送"""
        session = event.unified_msg_origin
        removed = await self.scheduler.remove_subscriber(session)
        if removed:
            yield event.plain_result("✅ 已取消订阅 F1 自动推送。")
        else:
            yield event.plain_result("ℹ️ 当前会话尚未订阅。发送 /f1 subscribe 可以订阅。")

    @f1.command("test")
    async def f1_test(self, event: AstrMessageEvent, season: str = "2025"):
        """测试插件各项查询功能，指定赛季（默认2025）"""
        yr = int(season) if season.isdigit() else 2025
        yield event.plain_result(f"🔧 正在测试 {yr} 赛季数据，请稍候...")

        results: list[tuple[str, bool, str]] = []  # (name, ok, detail)

        async def run(name: str, coro):
            try:
                val = await coro
                ok = bool(val)
                if ok:
                    if isinstance(val, list):
                        detail = f"✅ {len(val)} 条记录"
                    elif isinstance(val, dict):
                        detail = f"✅ {val.get('raceName') or val.get('Driver', {}).get('familyName', 'OK')}"
                    else:
                        detail = "✅"
                else:
                    detail = "⚠️ 无数据（可能尚未举行）"
                results.append((name, ok, detail))
            except Exception as e:
                results.append((name, False, f"❌ {e}"))

        # Jolpica tests
        await run("赛程",         api.get_current_schedule(yr))
        await run("正赛结果(R1)", api.get_race_result(1, yr))
        await run("排位(R1)",     api.get_qualifying_result(1, yr))
        await run("冲刺(R5)",     api.get_sprint_result(5, yr))
        await run("车手积分",     api.get_driver_standings(yr))
        await run("车队积分",     api.get_constructor_standings(yr))

        # OpenF1 tests
        async def _fp_test():
            s = await api.get_practice_session("1", yr)
            if not s:
                return None
            return await api.get_session_result(s["session_key"])

        await run("练习赛(FP1)",  _fp_test())

        lines = [f"📋 F1 插件测试报告 ({yr} 赛季)\n"]
        passed = 0
        for name, ok, detail in results:
            lines.append(f"  {'✅' if ok else '❌'} {name}: {detail}")
            if ok:
                passed += 1

        lines.append(f"\n共 {passed}/{len(results)} 项通过")
        yield event.plain_result("\n".join(lines))


