"""AstrBot command and lifecycle integration for F1 Notifier."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star, StarTools

from .src.astrbot_plugin_f1_notifier.api import DataError, F1Client
from .src.astrbot_plugin_f1_notifier.formatter import (
    format_table,
    schedule_table,
    weekend_table,
)
from .src.astrbot_plugin_f1_notifier.image_renderer import ImageRenderer
from .src.astrbot_plugin_f1_notifier.models import Table
from .src.astrbot_plugin_f1_notifier.scheduler import F1Scheduler

HELP_TEXT = """F1 Notifier
/f1 help — 帮助
/f1 schedule — 未来 5 站赛程
/f1 next — 下一站完整周末时间表
/f1 result [round] — 正赛结果，默认最近一站
/f1 qualifying [round] — 排位赛结果
/f1 sprint [round] — 冲刺赛结果
/f1 practice [1|2|3] — 最近一次指定练习赛结果
/f1 standings [drivers|teams] — 车手 / 车队积分榜
/f1 subscribe — 订阅当前会话
/f1 unsubscribe — 取消当前会话订阅
/f1 test [season] — 只读检查数据源，默认 2025
/f1 push <群UMO> <next|schedule|result|qualifying|sprint|practice|standings> [参数] — 管理员定向推送
/f1 subs — 管理员：列出全部订阅会话
/f1 subs add <会话UMO> — 管理员：添加订阅
/f1 subs remove <会话UMO> — 管理员：取消订阅

订阅推送：比赛周指定星期与时间的周末赛程、各节练习赛 / 排位赛 / 冲刺赛结果、正赛前 35 至 30 分钟已发布的发车顺序、正赛结果。
OpenF1 查询避开场次开始前 30 分钟至结束后 30 分钟；练习赛和冲刺排位赛结果至少等待结束后 31 分钟。
周末赛程默认周一 09:00，可在插件设置中选择周一 / 周二 / 周三及时间。
时间默认 Asia/Shanghai。结果在数据源发布后推送，可能延迟。
"""


def _parse_umo(value: str) -> tuple[str, str, str] | None:
    """Split a unified message origin without guessing missing fields.

    Args:
        value: Candidate ``platform:message_type:session`` string.

    Returns:
        The three components, or None if the value is not a supported UMO.
    """
    parts = value.split(":", 2)
    if (
        len(parts) != 3
        or parts[1] not in ("GroupMessage", "FriendMessage")
        or not parts[0].strip()
        or not parts[2].strip()
        or any(char.isspace() for char in value)
    ):
        return None
    return parts[0], parts[1], parts[2]


class F1NotifierPlugin(Star):
    """Keep AstrBot routing separate from provider and delivery state.

    Args:
        context: AstrBot runtime context.
        config: Plugin settings supplied by AstrBot.
    """

    def __init__(self, context: Context, config: AstrBotConfig) -> None:
        super().__init__(context)
        self.config = dict(config)
        self.config.setdefault("timezone", "Asia/Shanghai")
        try:
            ZoneInfo(self.config["timezone"])
        except (ZoneInfoNotFoundError, TypeError) as exc:
            raise ValueError("Invalid F1 display timezone") from exc
        self.config.setdefault("weekend_push_day", "周一")
        self.config.setdefault("weekend_push_time", "09:00")
        if self.config["weekend_push_day"] not in ("周一", "周二", "周三"):
            raise ValueError("weekend_push_day must be 周一, 周二, or 周三")
        push_time = self.config["weekend_push_time"]
        if (
            not isinstance(push_time, str)
            or re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", push_time) is None
        ):
            raise ValueError("weekend_push_time must use HH:MM between 00:00 and 23:59")
        for key, default, minimum, maximum in (
            ("result_poll_delay", 5, 0, 120),
            ("image_cache_max_age", 30, 1, 1440),
            ("image_cache_max_count", 50, 1, 200),
        ):
            value = self.config.get(key, default)
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not minimum <= value <= maximum
            ):
                raise ValueError(
                    f"{key} must be an integer between {minimum} and {maximum}"
                )
            self.config[key] = value
        self.client = F1Client()
        self.renderer: ImageRenderer | None = None
        self.scheduler: F1Scheduler | None = None
        self._ready = False

    async def initialize(self) -> None:
        """Restore subscriptions before starting polling or accepting commands."""
        if self._ready:
            return
        self.renderer = ImageRenderer(
            StarTools.get_data_dir("astrbot_plugin_f1_notifier") / "images",
            max_age_minutes=self.config["image_cache_max_age"],
            max_count=self.config["image_cache_max_count"],
        )
        try:
            await self.client.open()
            self.scheduler = F1Scheduler(
                self.client, self.get_kv_data, self.put_kv_data, self._send, self.config
            )
            await self.scheduler.initialize()
            self._ready = True
            self.scheduler.start()
        except BaseException:
            await self.client.close()
            await self.renderer.close()
            raise
        logger.info(
            "F1 notifier initialized with %s subscribers",
            self.scheduler.subscriber_count,
        )

    async def terminate(self) -> None:
        """Await polling and rendering before releasing owned resources."""
        self._ready = False
        try:
            if self.scheduler is not None:
                await self.scheduler.stop()
        finally:
            await self.client.close()
            if self.renderer is not None:
                await self.renderer.close()

    async def _send(self, origin: str, table: Table) -> bool:
        """Deliver one notification with a text fallback for image failures.

        Args:
            origin: AstrBot unified message origin.
            table: Shared notification content.

        Returns:
            Whether AstrBot found a platform and completed the send operation.
        """
        text = format_table(table)
        if self.config.get("enable_image_render") and self.renderer is not None:
            try:
                image = await self.renderer.render(table)
                return bool(
                    await self.context.send_message(
                        origin, MessageChain().file_image(str(image))
                    )
                )
            except Exception:
                logger.exception("F1 image delivery failed; falling back to text")
        return bool(
            await self.context.send_message(origin, MessageChain().message(text))
        )

    async def _reply(
        self,
        event: AstrMessageEvent,
        action: str,
        argument: str = "last",
        target: str | None = None,
    ):
        """Validate a query and render its result using the selected output mode.

        Args:
            event: Triggering message event.
            action: Query or subscription operation.
            argument: Optional command-specific user argument.
            target: Optional group UMO for an administrator's one-time push.

        Yields:
            An AstrBot text or image message result.
        """
        if not self._ready:
            yield event.plain_result("插件尚未就绪，请稍后重试。")
            return
        try:
            now = datetime.now(timezone.utc)
            if action in ("subscribe", "unsubscribe"):
                if action == "subscribe":
                    added = await self.scheduler.subscribe(event.unified_msg_origin)
                    text = (
                        "订阅成功，将推送本会话订阅后的比赛通知。"
                        if added
                        else "当前会话已订阅。"
                    )
                else:
                    removed = await self.scheduler.unsubscribe(event.unified_msg_origin)
                    text = "已取消当前会话订阅。" if removed else "当前会话尚未订阅。"
                yield event.plain_result(text)
                return
            if action == "standings":
                category = {
                    "driver": "drivers",
                    "drivers": "drivers",
                    "team": "teams",
                    "teams": "teams",
                }.get(argument.lower())
                if category is None:
                    yield event.plain_result(
                        "用法：/f1 standings drivers 或 /f1 standings teams"
                    )
                    return
                table = await self.client.standings(now.year, category)
            else:
                kind = action
                if action == "practice":
                    number = argument.lower().removeprefix("fp")
                    if number not in ("1", "2", "3"):
                        yield event.plain_result("用法：/f1 practice [1|2|3]")
                        return
                    kind = f"practice{number}"
                    argument = "last"
                elif action in ("race", "qualifying", "sprint") and argument != "last":
                    if (
                        not argument.isascii()
                        or not argument.isdecimal()
                        or not 1 <= int(argument) <= 99
                    ):
                        yield event.plain_result(
                            "站次必须是 1 到 99 的正整数，例如 /f1 result 2。"
                        )
                        return
                races = await self.client.schedule(now.year)
                if action in ("schedule", "next"):
                    upcoming = [race for race in races if race.start > now]
                    if len(upcoming) < (5 if action == "schedule" else 1):
                        try:
                            upcoming.extend(await self.client.schedule(now.year + 1))
                        except DataError:
                            if not upcoming:
                                raise
                    table = (
                        schedule_table(upcoming[:5], self.config["timezone"])
                        if action == "schedule" and upcoming
                        else weekend_table(upcoming[0], self.config["timezone"])
                        if upcoming
                        else None
                    )
                else:
                    if argument != "last":
                        race = next(
                            (r for r in races if r.round == int(argument)), None
                        )
                    else:
                        candidates = [
                            (s.start, r)
                            for r in races
                            for s in r.sessions
                            if s.key == kind and s.start <= now
                        ]
                        if not candidates:
                            races = await self.client.schedule(now.year - 1)
                            candidates = [
                                (s.start, r)
                                for r in races
                                for s in r.sessions
                                if s.key == kind and s.start <= now
                            ]
                        race = (
                            max(candidates, key=lambda item: item[0])[1]
                            if candidates
                            else None
                        )
                    table = (
                        await self.client.results(race, kind)
                        if race is not None
                        else None
                    )
            if table is None:
                yield event.plain_result(
                    "暂无已发布数据。赛程或结果可能尚未公布，请稍后重试。"
                )
                return
            if target is not None:
                try:
                    sent = await self._send(target, table)
                except Exception:
                    logger.exception("F1 proactive delivery failed for %s", target)
                    sent = False
                yield event.plain_result(
                    f"已向 {target} 推送。"
                    if sent
                    else "主动推送失败，请检查平台连接、目标群 UMO 和机器人发言权限。"
                )
                return
            if self.config.get("enable_image_render"):
                try:
                    image = await self.renderer.render(table)
                    yield event.image_result(str(image))
                    return
                except Exception:
                    logger.exception("F1 card rendering failed; falling back to text")
            yield event.plain_result(format_table(table))
        except DataError as exc:
            yield event.plain_result(str(exc))
        except Exception:
            logger.exception("F1 command %s failed", action)
            yield event.plain_result("查询失败，订阅与推送状态未丢失。请稍后重试。")

    @filter.command_group("f1")
    def f1(self):
        """Query F1 information and manage this conversation's subscription."""

    @f1.command("help")
    async def f1_help(self, event: AstrMessageEvent):
        """Show available commands.

        Args:
            event: Triggering message.

        Yields:
            Command help.
        """
        yield event.plain_result(HELP_TEXT)

    @f1.command("schedule")
    async def f1_schedule(self, event: AstrMessageEvent):
        """Show the next five races.

        Args:
            event: Triggering message.

        Yields:
            Schedule query result.
        """
        async for result in self._reply(event, "schedule"):
            yield result

    @f1.command("next")
    async def f1_next(self, event: AstrMessageEvent):
        """Show the next race weekend.

        Args:
            event: Triggering message.

        Yields:
            Full weekend timetable.
        """
        async for result in self._reply(event, "next"):
            yield result

    @f1.command("result")
    async def f1_result(self, event: AstrMessageEvent, round_num: str = "last"):
        """Show race classification.

        Args:
            event: Triggering message.
            round_num: Current-season round or last.

        Yields:
            Published classification or a pending-data message.
        """
        async for result in self._reply(event, "race", round_num):
            yield result

    @f1.command("qualifying")
    async def f1_qualifying(self, event: AstrMessageEvent, round_num: str = "last"):
        """Show qualifying classification.

        Args:
            event: Triggering message.
            round_num: Current-season round or last.

        Yields:
            Published qualifying times.
        """
        async for result in self._reply(event, "qualifying", round_num):
            yield result

    @f1.command("sprint")
    async def f1_sprint(self, event: AstrMessageEvent, round_num: str = "last"):
        """Show sprint classification.

        Args:
            event: Triggering message.
            round_num: Current-season round or last.

        Yields:
            Published sprint results.
        """
        async for result in self._reply(event, "sprint", round_num):
            yield result

    @f1.command("practice")
    async def f1_practice(self, event: AstrMessageEvent, session: str = "1"):
        """Show the latest selected practice classification.

        Args:
            event: Triggering message.
            session: Practice number, optionally prefixed by FP.

        Yields:
            Published practice results.
        """
        async for result in self._reply(event, "practice", session):
            yield result

    @f1.command("standings")
    async def f1_standings(self, event: AstrMessageEvent, category: str = "drivers"):
        """Show current-season championship standings.

        Args:
            event: Triggering message.
            category: drivers or teams.

        Yields:
            Championship standings.
        """
        async for result in self._reply(event, "standings", category):
            yield result

    @f1.command("subscribe")
    async def f1_subscribe(self, event: AstrMessageEvent):
        """Subscribe the current conversation.

        Args:
            event: Triggering message.

        Yields:
            Subscription status.
        """
        async for result in self._reply(event, "subscribe"):
            yield result

    @f1.command("unsubscribe")
    async def f1_unsubscribe(self, event: AstrMessageEvent):
        """Unsubscribe the current conversation.

        Args:
            event: Triggering message.

        Yields:
            Subscription status.
        """
        async for result in self._reply(event, "unsubscribe"):
            yield result

    @filter.permission_type(filter.PermissionType.ADMIN)
    @f1.command("push")
    async def f1_push(
        self,
        event: AstrMessageEvent,
        target: str = "",
        query: str = "next",
        argument: str = "",
    ):
        """Send one F1 query to a group without changing delivery state.

        Args:
            event: Administrator's triggering message.
            target: Full group UMO, including the platform instance ID.
            query: Existing F1 query type.
            argument: Optional round, practice number, or standings category.

        Yields:
            Validation, query, or delivery status for the administrator.
        """
        if not event.is_admin():
            yield event.plain_result("仅 AstrBot 管理员可以定向推送。")
            return
        parsed = _parse_umo(target)
        if parsed is None or parsed[1] != "GroupMessage":
            yield event.plain_result(
                "请提供目标群完整 UMO：平台实例ID:GroupMessage:群ID，可在目标群通过 /sid 获取。"
            )
            return
        action = {
            "next": "next",
            "schedule": "schedule",
            "result": "race",
            "qualifying": "qualifying",
            "sprint": "sprint",
            "practice": "practice",
            "standings": "standings",
        }.get(query.lower())
        if action is None or (action in ("next", "schedule") and argument):
            yield event.plain_result(
                "用法：/f1 push <群UMO> <next|schedule|result|qualifying|sprint|practice|standings> [参数]"
            )
            return
        argument = argument or {"practice": "1", "standings": "drivers"}.get(
            action, "last"
        )
        async for result in self._reply(event, action, argument, target=target):
            yield result

    @filter.permission_type(filter.PermissionType.ADMIN)
    @f1.command("subs")
    async def f1_subs(
        self, event: AstrMessageEvent, action: str = "list", target: str = ""
    ):
        """List, add, or remove subscriptions for any conversation.

        Args:
            event: Administrator's triggering message.
            action: list, add, or remove; defaults to list.
            target: Full UMO for add and remove.

        Yields:
            Subscription inventory or mutation status.
        """
        if not event.is_admin():
            yield event.plain_result("仅 AstrBot 管理员可以管理订阅。")
            return
        if not self._ready:
            yield event.plain_result("插件尚未就绪，请稍后重试。")
            return
        usage = (
            "用法：/f1 subs [list] | /f1 subs add <会话UMO> | /f1 subs remove <会话UMO>"
        )
        try:
            action = action.lower()
            if action == "list" and not target:
                subscriptions = await self.scheduler.subscriptions()
                if not subscriptions:
                    yield event.plain_result("当前没有订阅的会话。")
                    return
                timezone = ZoneInfo(self.config["timezone"])
                lines = [
                    f"已订阅会话（{len(subscriptions)}，时区 {self.config['timezone']}）："
                ]
                for index, (origin, since) in enumerate(subscriptions, 1):
                    parts = _parse_umo(origin)
                    if parts is None:
                        kind = "会话"
                    elif parts[1] == "FriendMessage":
                        kind = "私聊"
                    else:
                        kind = "群聊"
                    local_since = since.astimezone(timezone).strftime("%Y-%m-%d %H:%M")
                    lines.append(f"{index}. [{kind}] {origin} — {local_since}")
                yield event.plain_result("\n".join(lines))
                return
            if action in ("add", "remove") and _parse_umo(target) is not None:
                if action == "add":
                    added = await self.scheduler.subscribe(target)
                    yield event.plain_result(
                        f"已添加订阅：{target}。"
                        if added
                        else f"该会话已订阅：{target}。"
                    )
                else:
                    removed = await self.scheduler.unsubscribe(target)
                    yield event.plain_result(
                        f"已取消订阅：{target}。"
                        if removed
                        else f"该会话尚未订阅：{target}。"
                    )
                return
            yield event.plain_result(usage)
        except Exception:
            logger.exception("F1 subscription management failed")
            yield event.plain_result("订阅管理失败，请查看插件日志。")

    @f1.command("test")
    async def f1_test(self, event: AstrMessageEvent, season: str = "2025"):
        """Check providers without changing subscriptions or delivery state.

        Args:
            event: Triggering message.
            season: Historical or current championship year.

        Yields:
            Provider checks, including unavailable endpoints.
        """
        if not self._ready:
            yield event.plain_result("插件尚未就绪，请稍后重试。")
            return
        if (
            not season.isascii()
            or not season.isdecimal()
            or not 1950 <= int(season) <= datetime.now(timezone.utc).year + 1
        ):
            yield event.plain_result("赛季必须是 1950 到明年之间的四位年份。")
            return
        try:
            races = await self.client.schedule(int(season))
            yield event.plain_result(f"{season} 赛程：{len(races)} 站")
            if not races:
                return
            for kind in ("race", "qualifying", "sprint", "practice1", "grid"):
                race = next(
                    (
                        r
                        for r in reversed(races)
                        if any(
                            s.key == ("race" if kind == "grid" else kind)
                            and s.start <= datetime.now(timezone.utc)
                            for s in r.sessions
                        )
                    ),
                    None,
                )
                if race is None:
                    yield event.plain_result(f"{kind}：暂无场次")
                    continue
                try:
                    table = await self.client.results(race, kind)
                    yield event.plain_result(
                        f"{kind}：{len(table.rows)} 条记录"
                        if table
                        else f"{kind}：暂无已发布数据"
                    )
                except DataError as exc:
                    yield event.plain_result(f"{kind}：{exc}")
            for category in ("drivers", "teams"):
                try:
                    table = await self.client.standings(int(season), category)
                    yield event.plain_result(
                        f"{category}：{len(table.rows)} 条记录"
                        if table
                        else f"{category}：暂无已发布数据"
                    )
                except DataError as exc:
                    yield event.plain_result(f"{category}：{exc}")
        except DataError as exc:
            yield event.plain_result(str(exc))
        except Exception:
            logger.exception("F1 diagnostic command failed")
            yield event.plain_result("数据源检查失败，请查看插件日志。")
