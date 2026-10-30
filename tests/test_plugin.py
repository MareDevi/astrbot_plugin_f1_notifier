"""Load and command tests against AstrBot's actual plugin API."""

import importlib.util
import sys
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from src.astrbot_plugin_f1_notifier.models import Race, Session, Table


@pytest.fixture
def module(tmp_path, monkeypatch):
    monkeypatch.setenv("ASTRBOT_ROOT", str(tmp_path / "astrbot"))
    name = "f1_notifier_under_test"
    spec = importlib.util.spec_from_file_location(
        name,
        Path(__file__).resolve().parents[1] / "__init__.py",
        submodule_search_locations=[str(Path(__file__).resolve().parents[1])],
    )
    package = importlib.util.module_from_spec(spec)
    sys.modules[name] = package
    path = Path(__file__).resolve().parents[1] / "main.py"
    spec = importlib.util.spec_from_file_location(f"{name}.main", path)
    loaded = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


@pytest.fixture
async def plugin(module, tmp_path, monkeypatch):
    monkeypatch.setattr(module.StarTools, "get_data_dir", lambda *args: tmp_path)
    context = AsyncMock()
    instance = module.F1NotifierPlugin(context, {})
    store = {}
    instance.get_kv_data = AsyncMock(
        side_effect=lambda key, default: deepcopy(store.get(key, default))
    )

    async def save(key, value):
        store[key] = deepcopy(value)

    instance.put_kv_data = save
    await instance.initialize()
    await instance.scheduler.stop()
    yield instance
    await instance.terminate()


class Event:
    unified_msg_origin = "test:GroupMessage:1"

    def plain_result(self, text):
        return text

    def image_result(self, path):
        return ("image", path)


async def test_initialize_uses_real_astrbot_api_and_restores_state(plugin):
    assert plugin.scheduler.subscriber_count == 0
    assert plugin.client._session is not None


async def test_invalid_round_does_not_call_provider(plugin):
    plugin.client.schedule = AsyncMock()
    replies = [r async for r in plugin.f1_result(Event(), "0")]
    assert "正整数" in replies[0]
    plugin.client.schedule.assert_not_called()


async def test_invalid_standings_category_has_usage(plugin):
    replies = [r async for r in plugin.f1_standings(Event(), "wrong")]
    assert "drivers" in replies[0] and "teams" in replies[0]


async def test_text_mode_does_not_invoke_renderer(plugin):
    plugin.client.standings = AsyncMock(
        return_value=Table(
            "车手积分榜", columns=("名次", "车手"), rows=(("1", "Winner"),)
        )
    )
    plugin.renderer.render = AsyncMock(side_effect=AssertionError("must not render"))
    replies = [r async for r in plugin.f1_standings(Event(), "drivers")]
    assert "Winner" in replies[0]
    plugin.renderer.render.assert_not_called()


async def test_image_failure_falls_back_to_text(plugin):
    plugin.config["enable_image_render"] = True
    plugin.client.standings = AsyncMock(
        return_value=Table("积分榜", rows=(("1", "Winner"),))
    )
    plugin.renderer.render = AsyncMock(side_effect=OSError("font unavailable"))
    replies = [r async for r in plugin.f1_standings(Event(), "drivers")]
    assert "Winner" in replies[0]


async def test_subscription_command_is_persistent(plugin):
    replies = [r async for r in plugin.f1_subscribe(Event())]
    assert "订阅成功" in replies[0]
    assert plugin.scheduler.subscriber_count == 1
    replies = [r async for r in plugin.f1_unsubscribe(Event())]
    assert "取消" in replies[0]
    assert plugin.scheduler.subscriber_count == 0


async def test_next_selects_upcoming_weekend(plugin):
    now = datetime.now(timezone.utc)
    old = Race(
        now.year, 1, "Old", "", "", "", (Session("race", now - timedelta(days=7)),)
    )
    new = Race(
        now.year, 2, "New", "", "", "", (Session("race", now + timedelta(days=7)),)
    )
    plugin.client.schedule = AsyncMock(return_value=[old, new])
    replies = [r async for r in plugin.f1_next(Event())]
    assert "New" in replies[0] and "Old" not in replies[0]


async def test_unsupported_platform_does_not_mark_delivery_success(plugin):
    plugin.context.send_message.return_value = False
    assert not await plugin._send("test:GroupMessage:1", Table("Test"))


async def test_schedule_queries_next_year_during_offseason(plugin):
    now = datetime.now(timezone.utc)
    future = Race(
        now.year + 1,
        1,
        "Next season",
        "",
        "",
        "",
        (Session("race", now + timedelta(days=365)),),
    )
    plugin.client.schedule = AsyncMock(side_effect=[[], [future]])
    replies = [r async for r in plugin.f1_next(Event())]
    assert "Next season" in replies[0]


@pytest.mark.parametrize(
    "settings",
    [
        {"weekend_push_day": "周四"},
        {"weekend_push_day": 1},
        {"weekend_push_time": "24:00"},
        {"weekend_push_time": "09:60"},
        {"weekend_push_time": "9:00"},
        {"weekend_push_time": "09:00:00"},
        {"weekend_push_time": "０９:００"},
        {"weekend_push_time": None},
    ],
)
def test_invalid_weekend_reminder_settings_rejected(module, settings):
    with pytest.raises(ValueError, match="weekend_push_"):
        module.F1NotifierPlugin(AsyncMock(), settings)
