"""AstrBot plugin: F1 Notifier

Pushes F1 race notifications automatically and provides on-demand
query commands for race information.

Commands (prefix /f1):
  /f1 schedule           — upcoming race schedule
  /f1 next               — next race full timetable
  /f1 result [round]     — race result (default: latest)
  /f1 qualifying [round] — qualifying result (default: latest)
  /f1 sprint [round]     — sprint race result
  /f1 practice [1|2|3]   — practice session fastest laps
  /f1 standings drivers  — driver championship standings
  /f1 standings teams    — constructor standings
  /f1 subscribe          — subscribe to auto notifications
  /f1 unsubscribe        — unsubscribe from notifications
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

from .src.astrbot_plugin_f1_notifier import api
from .src.astrbot_plugin_f1_notifier import formatter as fmt
from .src.astrbot_plugin_f1_notifier import image_renderer as img
from .src.astrbot_plugin_f1_notifier.models import Failure, Success
from .src.astrbot_plugin_f1_notifier.scheduler import F1Scheduler

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
    "1.0.2",
    "https://github.com/MareDevi/astrbot_plugin_f1_notifier",
)
class F1NotifierPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig) -> None:
        super().__init__(context)
        self.config = config
        self.scheduler = F1Scheduler(self, context, config)

    @property
    def _image_mode(self) -> bool:
        return bool(self.config.get("enable_image_render", False))

    async def _render_or_text(
        self, event: AstrMessageEvent, text: str, image_path: str
    ):
        """Yield image result if image mode is on, otherwise plain text."""
        if self._image_mode:
            try:
                return event.image_result(image_path)
            except Exception as e:
                logger.warning(
                    f"[F1Notifier] Image render failed, fallback to text: {e}"
                )
        return event.plain_result(text)

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
        result = await api.get_current_schedule()
        match result:
            case Success(value=races):
                text = fmt.format_schedule(races)
                image_path = img.render_schedule(races)
                yield await self._render_or_text(event, text, image_path)
            case Failure(error=err):
                logger.error(f"[F1Notifier] /f1 schedule error: {err}")
                yield event.plain_result("❌ 获取赛程失败，请稍后重试。")

    @f1.command("next")
    async def f1_next(self, event: AstrMessageEvent):
        """查看下一站完整时间表"""
        result = await api.get_current_schedule()
        match result:
            case Success(value=races):
                now = datetime.now(timezone.utc)
                next_race = None
                for race in races:
                    dt = fmt.race_utc(race)
                    if dt is not None and dt >= now:
                        next_race = race
                        break
                if next_race is None:
                    yield event.plain_result(
                        "📅 本赛季剩余赛程已全部完成，期待下赛季！"
                    )
                else:
                    text = fmt.format_next_race(next_race)
                    image_path = img.render_next_race(next_race)
                    yield await self._render_or_text(event, text, image_path)
            case Failure(error=err):
                logger.error(f"[F1Notifier] /f1 next error: {err}")
                yield event.plain_result("❌ 获取下一站信息失败，请稍后重试。")

    @f1.command("result")
    async def f1_result(self, event: AstrMessageEvent, round_num: str = "last"):
        """查看正赛结果，可指定站次 round"""
        round_arg: int | str = int(round_num) if round_num.isdigit() else "last"
        result = await api.get_race_result(round_arg)
        match result:
            case Success(value=race) if race.race_results:
                text = fmt.format_race_result(race)
                image_path = img.render_race_result(race)
                yield await self._render_or_text(event, text, image_path)
            case Success():
                yield event.plain_result("⏳ 正赛结果暂未公布，请比赛结束后再试。")
            case Failure(error=err):
                logger.error(f"[F1Notifier] /f1 result error: {err}")
                yield event.plain_result("❌ 获取比赛结果失败，请稍后重试。")

    @f1.command("qualifying")
    async def f1_qualifying(self, event: AstrMessageEvent, round_num: str = "last"):
        """查看排位赛结果，可指定站次 round"""
        round_arg: int | str = int(round_num) if round_num.isdigit() else "last"
        result = await api.get_qualifying_result(round_arg)
        match result:
            case Success(value=race) if race.qualifying_results:
                text = fmt.format_qualifying_result(race)
                image_path = img.render_qualifying_result(race)
                yield await self._render_or_text(event, text, image_path)
            case Success():
                yield event.plain_result("⏳ 排位赛结果暂未公布，请稍后再试。")
            case Failure(error=err):
                logger.error(f"[F1Notifier] /f1 qualifying error: {err}")
                yield event.plain_result("❌ 获取排位赛结果失败，请稍后重试。")

    @f1.command("sprint")
    async def f1_sprint(self, event: AstrMessageEvent, round_num: str = "last"):
        """查看冲刺赛结果，可指定站次 round"""
        round_arg: int | str = int(round_num) if round_num.isdigit() else "last"
        result = await api.get_sprint_result(round_arg)
        match result:
            case Success(value=race) if race.sprint_results:
                text = fmt.format_sprint_result(race)
                image_path = img.render_sprint_result(race)
                yield await self._render_or_text(event, text, image_path)
            case Success():
                yield event.plain_result("⏳ 冲刺赛结果暂未公布，或该站无冲刺赛。")
            case Failure(error=err):
                logger.error(f"[F1Notifier] /f1 sprint error: {err}")
                yield event.plain_result("❌ 获取冲刺赛结果失败，请稍后重试。")

    @f1.command("practice")
    async def f1_practice(self, event: AstrMessageEvent, session: str = "1"):
        """查看练习赛最快圈速。session: 1/2/3（默认1，也可输入 fp1/fp2/fp3）"""
        normalized = session.lower().removeprefix("fp") or "1"
        if normalized not in ("1", "2", "3"):
            yield event.plain_result(
                "❌ 请输入有效的练习赛场次：1、2、或 3（如 /f1 practice 2）"
            )
            return

        session_result = await api.get_practice_session(normalized)
        match session_result:
            case Failure(error=err):
                logger.warning(f"[F1Notifier] /f1 practice session lookup: {err}")
                yield event.plain_result(
                    f"⏳ 暂未找到 FP{normalized} 练习赛数据，请练习赛结束后再试。"
                )
                return
            case Success(value=of1_session):
                sk = of1_session.session_key
                results_result, drivers_result = await asyncio.gather(
                    api.get_session_result(sk),
                    api.get_drivers_for_session(sk),
                )
                match (results_result, drivers_result):
                    case (Success(value=results), Success(value=drivers_list)) if (
                        results
                    ):
                        drivers_by_num = {d.driver_number: d for d in drivers_list}
                        text = fmt.format_practice_result(
                            of1_session, results, drivers_by_num, normalized
                        )
                        image_path = img.render_practice_result(
                            of1_session, results, drivers_by_num, normalized
                        )
                        yield await self._render_or_text(event, text, image_path)
                    case (Success(), Failure(error=err)):
                        logger.error(f"[F1Notifier] /f1 practice drivers error: {err}")
                        yield event.plain_result(
                            "❌ 获取练习赛车手数据失败，请稍后重试。"
                        )
                    case (Success(), _):
                        yield event.plain_result(
                            f"⏳ FP{normalized} 结果数据暂未就绪，请练习赛结束后再试。"
                        )
                    case (Failure(error=err), _):
                        logger.error(f"[F1Notifier] /f1 practice error: {err}")
                        yield event.plain_result("❌ 获取练习赛数据失败，请稍后重试。")

    @f1.command("standings")
    async def f1_standings(self, event: AstrMessageEvent, category: str = "drivers"):
        """查看积分榜。category: drivers（默认）或 teams"""
        match category.lower():
            case "teams" | "constructors" | "team" | "车队":
                result = await api.get_constructor_standings()
                match result:
                    case Success(value=standings):
                        text = fmt.format_constructor_standings(standings)
                        image_path = img.render_constructor_standings(standings)
                        yield await self._render_or_text(event, text, image_path)
                    case Failure(error=err):
                        logger.error(f"[F1Notifier] /f1 standings teams error: {err}")
                        yield event.plain_result("❌ 获取车队积分榜失败，请稍后重试。")
            case _:
                result = await api.get_driver_standings()
                match result:
                    case Success(value=standings):
                        text = fmt.format_driver_standings(standings)
                        image_path = img.render_driver_standings(standings)
                        yield await self._render_or_text(event, text, image_path)
                    case Failure(error=err):
                        logger.error(f"[F1Notifier] /f1 standings drivers error: {err}")
                        yield event.plain_result("❌ 获取车手积分榜失败，请稍后重试。")

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
            yield event.plain_result(
                "ℹ️ 当前会话已经订阅过了。发送 /f1 unsubscribe 可以取消。"
            )

    @f1.command("unsubscribe")
    async def f1_unsubscribe(self, event: AstrMessageEvent):
        """取消当前会话的自动推送"""
        session = event.unified_msg_origin
        removed = await self.scheduler.remove_subscriber(session)
        if removed:
            yield event.plain_result("✅ 已取消订阅 F1 自动推送。")
        else:
            yield event.plain_result(
                "ℹ️ 当前会话尚未订阅。发送 /f1 subscribe 可以订阅。"
            )

    @f1.command("test")
    async def f1_test(self, event: AstrMessageEvent, season: str = "2025"):
        """测试插件各项查询功能，指定赛季（默认2025）"""
        yr = int(season) if season.isdigit() else 2025
        yield event.plain_result(f"🔧 正在测试 {yr} 赛季数据，请稍候...")

        async def run(name: str, coro) -> tuple[str, bool, str]:
            r = await coro
            match r:
                case Success(value=val):
                    match val:
                        case [*items]:
                            return (name, True, f"✅ {len(items)} 条记录")
                        case obj:
                            label = getattr(obj, "race_name", None) or getattr(
                                obj, "driver", None
                            )
                            detail = f"✅ {label}" if label else "✅"
                            return (name, True, detail)
                case Failure(error=err):
                    return (name, False, f"❌ {err}")
            return (name, False, "❌ unknown")

        async def _fp_test():
            s = await api.get_practice_session("1", yr)
            match s:
                case Success(value=session):
                    return await api.get_session_result(session.session_key)
                case Failure() as f:
                    return f

        results: list[tuple[str, bool, str]] = list(
            await asyncio.gather(
                run("赛程", api.get_current_schedule(yr)),
                run("正赛结果(R1)", api.get_race_result(1, yr)),
                run("排位(R1)", api.get_qualifying_result(1, yr)),
                run("冲刺(R5)", api.get_sprint_result(5, yr)),
                run("车手积分", api.get_driver_standings(yr)),
                run("车队积分", api.get_constructor_standings(yr)),
                run("练习赛(FP1)", _fp_test()),
            )
        )

        lines = [f"📋 F1 插件测试报告 ({yr} 赛季)\n"]
        passed = sum(1 for _, ok, _ in results if ok)
        for name, ok, detail in results:
            lines.append(f"  {'✅' if ok else '❌'} {name}: {detail}")
        lines.append(f"\n共 {passed}/{len(results)} 项通过")
        yield event.plain_result("\n".join(lines))
