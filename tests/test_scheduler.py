"""Delivery and lifecycle tests using the real notification scheduler."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from src.astrbot_plugin_f1_notifier import api
from src.astrbot_plugin_f1_notifier.models import Race, Session, Table
from src.astrbot_plugin_f1_notifier.scheduler import F1Scheduler


@pytest.fixture
def setup_scheduler():
    now = datetime(2026, 3, 15, 10, tzinfo=timezone.utc)
    race = Race(
        2026,
        2,
        "China",
        "shanghai",
        "Shanghai",
        "China",
        (Session("race", now - timedelta(hours=4)),),
    )
    store = {"f1_subscribers": ["one:GroupMessage:1", "two:GroupMessage:2"]}

    async def load(key, default):
        return deepcopy(store.get(key, default))

    async def save(key, value):
        store[key] = deepcopy(value)

    client = AsyncMock()
    client.schedule.return_value = [race]
    client.results.return_value = Table("正赛结果", rows=(("1", "Winner"),))
    send = AsyncMock(return_value=True)
    scheduler = F1Scheduler(client, load, save, send, {"result_poll_delay": 0})
    return scheduler, client, send, store, now


async def test_initialize_loads_subscriptions_before_commands(setup_scheduler):
    scheduler, _, _, store, now = setup_scheduler
    await scheduler.initialize(now)
    assert scheduler.subscriber_count == 2
    assert not await scheduler.subscribe("one:GroupMessage:1", now)
    assert store["f1_v2_state"]["version"] == 2


async def test_failed_recipient_retries_without_resending_success(setup_scheduler):
    scheduler, _, send, _, now = setup_scheduler
    await scheduler.initialize(now)
    send.side_effect = [True, False, True]
    await scheduler.tick(now)
    await scheduler.tick(now + timedelta(minutes=1))
    recipients = [call.args[0] for call in send.call_args_list]
    assert recipients == [
        "one:GroupMessage:1",
        "two:GroupMessage:2",
        "two:GroupMessage:2",
    ]


async def test_restart_does_not_replay_successful_results(setup_scheduler):
    scheduler, client, send, store, now = setup_scheduler
    await scheduler.initialize(now)
    await scheduler.tick(now)
    send.reset_mock()
    restarted = F1Scheduler(client, scheduler._load, scheduler._save, send, {})
    await restarted.initialize(now)
    await restarted.tick(now + timedelta(minutes=1))
    assert not send.called
    assert "2026:2:race" in store["f1_v2_state"]["delivered"]


async def test_new_subscription_does_not_replay_old_results(setup_scheduler):
    scheduler, _, send, store, now = setup_scheduler
    store.clear()
    await scheduler.initialize(now)
    await scheduler.subscribe("new:GroupMessage:3", now)
    await scheduler.tick(now)
    assert not send.called


async def test_last_weekend_results_checked_after_next_round_selected(setup_scheduler):
    scheduler, client, send, _, now = setup_scheduler
    previous = client.schedule.return_value[0]
    future = Race(
        2026,
        3,
        "Japan",
        "suzuka",
        "Suzuka",
        "Japan",
        (Session("race", now + timedelta(days=7)),),
    )
    client.schedule.return_value = [previous, future]
    await scheduler.initialize(now)
    await scheduler.tick(now)
    assert send.await_count == 2
    assert client.results.call_args.args == (previous, "race")


async def test_no_subscribers_means_no_api_requests(setup_scheduler):
    scheduler, client, _, store, now = setup_scheduler
    store.clear()
    await scheduler.initialize(now)
    await scheduler.tick(now)
    client.schedule.assert_not_called()


async def test_subscription_write_failure_keeps_previous_state(setup_scheduler):
    scheduler, _, _, _, now = setup_scheduler
    await scheduler.initialize(now)
    scheduler._save = AsyncMock(side_effect=OSError("disk full"))
    with pytest.raises(OSError):
        await scheduler.subscribe("new:GroupMessage:3", now)
    assert scheduler.subscriber_count == 2


async def test_unsubscribe_and_resubscribe_do_not_replay_delivered_event(
    setup_scheduler,
):
    scheduler, _, send, _, now = setup_scheduler
    await scheduler.initialize(now)
    await scheduler.tick(now)
    await scheduler.unsubscribe("one:GroupMessage:1")
    await scheduler.subscribe("one:GroupMessage:1", now)
    send.reset_mock()
    await scheduler.tick(now + timedelta(minutes=1))
    assert not send.called


async def test_unload_awaits_background_cancellation(setup_scheduler):
    scheduler, _, _, _, now = setup_scheduler
    await scheduler.initialize(now)
    scheduler.start()
    task = scheduler._task
    await scheduler.stop()
    assert task.done()
    assert scheduler._task is None


async def test_previous_season_delivery_does_not_suppress_current_round(
    setup_scheduler,
):
    scheduler, _, send, store, now = setup_scheduler
    store["f1_v2_state"] = {
        "version": 2,
        "subscribers": {"one:GroupMessage:1": (now - timedelta(days=10)).isoformat()},
        "delivered": {
            "2025:2:race": {
                "expires": (now + timedelta(days=1)).isoformat(),
                "recipients": ["one:GroupMessage:1"],
            }
        },
    }
    await scheduler.initialize(now)
    await scheduler.tick(now)
    assert send.await_count == 1
    assert "2026:2:race" in store["f1_v2_state"]["delivered"]


async def test_invalid_persisted_state_is_not_overwritten(setup_scheduler):
    scheduler, _, _, store, now = setup_scheduler
    store["f1_v2_state"] = {
        "version": 2,
        "subscribers": {"one": "invalid"},
        "delivered": {},
    }
    before = deepcopy(store)
    with pytest.raises(ValueError):
        await scheduler.initialize(now)
    assert store == before


async def test_pending_results_not_marked_delivered(setup_scheduler):
    scheduler, client, send, store, now = setup_scheduler
    client.results.return_value = None
    await scheduler.initialize(now)
    await scheduler.tick(now)
    assert not send.called
    assert not store["f1_v2_state"]["delivered"]


@pytest.mark.parametrize("stage", ["schedule", "results"])
async def test_rate_limit_stays_pending_without_poll_log_spam(
    setup_scheduler, caplog, stage
):
    scheduler, client, send, store, now = setup_scheduler
    await scheduler.initialize(now)
    request = getattr(client, stage)
    request.side_effect = api.RateLimitError("数据源限流，请稍后重试。")
    for minute in range(3):
        await scheduler.tick(now + timedelta(minutes=minute))
    assert not caplog.records
    send.assert_not_called()
    assert not store["f1_v2_state"]["delivered"]

    request.side_effect = None
    await scheduler.tick(now + timedelta(minutes=3))
    assert send.await_count == 2
    assert "2026:2:race" in store["f1_v2_state"]["delivered"]


@pytest.mark.parametrize("stage", ["schedule", "results"])
async def test_other_provider_errors_still_logged(setup_scheduler, caplog, stage):
    scheduler, client, _, _, now = setup_scheduler
    await scheduler.initialize(now)
    getattr(client, stage).side_effect = api.DataError("provider unavailable")
    await scheduler.tick(now)
    assert len(caplog.records) == 1
    assert caplog.records[0].exc_info is not None


@pytest.mark.parametrize(
    ("weekday", "due"),
    [
        ("周一", datetime(2026, 3, 9, 13, 45, tzinfo=timezone.utc)),
        ("周二", datetime(2026, 3, 10, 13, 45, tzinfo=timezone.utc)),
        ("周三", datetime(2026, 3, 11, 13, 45, tzinfo=timezone.utc)),
    ],
)
async def test_weekend_reminder_at_configured_local_time(setup_scheduler, weekday, due):
    scheduler, client, send, store, _ = setup_scheduler
    first = datetime(2026, 3, 13, 3, tzinfo=timezone.utc)
    race = Race(
        2026,
        2,
        "China",
        "shanghai",
        "Shanghai",
        "China",
        (Session("practice1", first), Session("race", first + timedelta(days=2))),
    )
    client.schedule.return_value = [race]
    scheduler.config.update(
        timezone="Asia/Shanghai", weekend_push_day=weekday, weekend_push_time="21:45"
    )
    await scheduler.initialize(due - timedelta(days=1))
    await scheduler.tick(due - timedelta(seconds=1))
    send.assert_not_called()
    await scheduler.tick(due)
    assert send.await_count == 2
    assert all(call.args[1].title == "比赛周开始提醒" for call in send.call_args_list)
    assert "2026:2:weekend" in store["f1_v2_state"]["delivered"]
    client.results.assert_not_called()

    send.reset_mock()
    restarted = F1Scheduler(
        client, scheduler._load, scheduler._save, send, scheduler.config
    )
    await restarted.initialize(due)
    await restarted.tick(due + timedelta(minutes=1))
    send.assert_not_called()


@pytest.mark.parametrize("zone", ["Asia/Shanghai", "America/New_York"])
async def test_weekend_defaults_follow_local_week_across_dst(setup_scheduler, zone):
    scheduler, client, send, _, _ = setup_scheduler
    local_zone = ZoneInfo(zone)
    # The race is on Monday in Shanghai; the first session still anchors its week.
    first = datetime(2026, 3, 8, 10, tzinfo=timezone.utc)
    client.schedule.return_value = [
        Race(
            2026,
            1,
            "Australia",
            "",
            "",
            "",
            (
                Session("practice1", first),
                Session("race", datetime(2026, 3, 8, 18, tzinfo=timezone.utc)),
            ),
        )
    ]
    scheduler.config["timezone"] = zone
    due = datetime(2026, 3, 2, 9, tzinfo=local_zone).astimezone(timezone.utc)
    await scheduler.initialize(due)
    await scheduler.tick(due - timedelta(seconds=1))
    send.assert_not_called()
    await scheduler.tick(due)
    assert send.await_count == 2


async def test_weekend_late_subscription_retries_only_until_first_session(
    setup_scheduler,
):
    scheduler, client, send, store, _ = setup_scheduler
    store.clear()
    first = datetime(2026, 3, 13, 3, tzinfo=timezone.utc)
    client.schedule.return_value = [
        Race(
            2026,
            2,
            "China",
            "",
            "",
            "",
            (Session("practice1", first), Session("race", first + timedelta(days=2))),
        )
    ]
    now = first - timedelta(days=2)
    await scheduler.initialize(now)
    await scheduler.subscribe("new:GroupMessage:3", now)
    send.side_effect = [False, True]
    await scheduler.tick(now)
    await scheduler.tick(now + timedelta(minutes=1))
    assert send.await_count == 2
    assert store["f1_v2_state"]["delivered"]["2026:2:weekend"]["recipients"] == [
        "new:GroupMessage:3"
    ]
    await scheduler.subscribe("late:GroupMessage:4", first)
    send.reset_mock()
    await scheduler.tick(first)
    send.assert_not_called()


async def test_weekend_reminder_loads_next_season_in_new_year_week(setup_scheduler):
    scheduler, client, send, _, _ = setup_scheduler
    now = datetime(2025, 12, 29, 1, tzinfo=timezone.utc)
    first = datetime(2026, 1, 2, 3, tzinfo=timezone.utc)
    race = Race(
        2026,
        1,
        "New Year",
        "",
        "",
        "",
        (Session("practice1", first), Session("race", first + timedelta(days=2))),
    )
    client.schedule.side_effect = lambda season: [race] if season == 2026 else []
    await scheduler.initialize(now)
    await scheduler.tick(now)
    assert send.await_count == 2
