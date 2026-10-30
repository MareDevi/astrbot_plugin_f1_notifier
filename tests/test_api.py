"""Contract tests for the F1 data client and normalized models."""

from datetime import datetime, timezone

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from src.astrbot_plugin_f1_notifier import api
from src.astrbot_plugin_f1_notifier.models import Race, Session, parse_datetime


@pytest.fixture
async def server():
    app = web.Application()
    state = {"calls": 0, "status": 200, "payload": {"ok": True}}

    async def respond(request):
        state["calls"] += 1
        return web.json_response(
            state["payload"], status=state["status"], headers={"Retry-After": "60"}
        )

    app.router.add_get("/{path:.*}", respond)
    async with TestServer(app) as instance:
        yield str(instance.make_url("")).rstrip("/"), state


@pytest.fixture
async def client(server):
    base, _ = server
    instance = api.F1Client(jolpica_base=base, openf1_base=base)
    await instance.open()
    yield instance
    await instance.close()


def test_timestamp_rejects_unknown_timezone():
    assert parse_datetime("2026-03-08T04:00:00Z") == datetime(
        2026, 3, 8, 4, tzinfo=timezone.utc
    )
    with pytest.raises(ValueError):
        parse_datetime("2026-03-08T04:00:00")


def test_schedule_keeps_sprint_qualifying_and_omits_unknown_time():
    race = Race.from_jolpica(
        {
            "season": "2026",
            "round": "2",
            "raceName": "Chinese Grand Prix",
            "date": "2026-03-15",
            "time": "07:00:00Z",
            "Circuit": {
                "circuitId": "shanghai",
                "circuitName": "Shanghai",
                "Location": {"country": "China"},
            },
            "FirstPractice": {"date": "2026-03-13", "time": "03:30:00Z"},
            "SecondPractice": {"date": "2026-03-13"},
            "SprintQualifying": {"date": "2026-03-13", "time": "07:30:00Z"},
            "Sprint": {"date": "2026-03-14", "time": "03:00:00Z"},
        }
    )
    assert race.key == "2026:2"
    assert [s.key for s in race.sessions] == [
        "practice1",
        "sprint_qualifying",
        "sprint",
        "race",
    ]


async def test_http_cache_is_per_instance_and_empty_list_is_not_cached(client, server):
    base, state = server
    assert await client._get(base, "/sample", ttl=60) == {"ok": True}
    await client._get(base, "/sample", ttl=60)
    assert state["calls"] == 1
    state["payload"] = []
    await client._get(base, "/empty", ttl=60)
    state["payload"] = [{"position": 1}]
    assert await client._get(base, "/empty", ttl=60) == [{"position": 1}]
    assert state["calls"] == 3


async def test_rate_limit_sets_cooldown_without_caching_error(client, server):
    base, state = server
    state["status"] = 429
    with pytest.raises(api.DataError, match="限流"):
        await client._get(base, "/limited")
    state["status"] = 200
    with pytest.raises(api.DataError, match="限流"):
        await client._get(base, "/limited")
    assert state["calls"] == 1


async def test_session_matching_uses_time_and_type_not_latest(client, server):
    _, state = server
    start = datetime(2026, 3, 15, 7, tzinfo=timezone.utc)
    race = Race(
        2026, 2, "China", "shanghai", "Shanghai", "China", (Session("race", start),)
    )
    state["payload"] = [
        {
            "session_key": 99,
            "session_name": "Race",
            "date_start": "2026-03-22T07:00:00Z",
        },
        {
            "session_key": 12,
            "session_name": "Qualifying",
            "date_start": "2026-03-15T07:00:00Z",
        },
        {
            "session_key": 13,
            "session_name": "Race",
            "date_start": "2026-03-15T07:05:00Z",
        },
    ]
    assert (await client.openf1_session(race, "race"))["session_key"] == 13


async def test_result_pending_is_not_previous_round(client, server):
    _, state = server
    race = Race(
        2026,
        2,
        "China",
        "shanghai",
        "Shanghai",
        "China",
        (Session("race", datetime(2026, 3, 15, 7, tzinfo=timezone.utc)),),
    )
    state["payload"] = {
        "MRData": {
            "RaceTable": {
                "Races": [
                    {"season": "2026", "round": "1", "Results": [{"position": "1"}]}
                ]
            }
        }
    }
    assert await client.results(race, "race") is None


async def test_qualifying_null_times_and_disqualification(client, server):
    _, state = server
    race = Race(
        2026,
        2,
        "China",
        "shanghai",
        "Shanghai",
        "China",
        (Session("race", datetime(2026, 3, 15, 7, tzinfo=timezone.utc)),),
    )
    state["payload"] = {
        "MRData": {
            "RaceTable": {
                "Races": [
                    {
                        "season": "2026",
                        "round": "2",
                        "QualifyingResults": [
                            {
                                "position": "1",
                                "Driver": {
                                    "givenName": "Lando",
                                    "familyName": "Norris",
                                },
                                "Constructor": {"name": "McLaren"},
                                "Q1": "1:30.000",
                                "Q2": None,
                            },
                        ],
                    }
                ]
            }
        }
    }
    table = await client.results(race, "qualifying")
    assert table is not None
    assert table.rows[0][1] == "Lando Norris"
    assert table.rows[0][-2:] == ("—", "—")


async def test_unpublished_openf1_dataset_is_pending_not_an_error(client, server):
    base, state = server
    state["status"] = 404
    assert await client._get(base, "/starting_grid", {"session_key": 9693}) == []
    assert not client._cache


async def test_session_matching_crosses_utc_midnight(client, server):
    _, state = server
    race = Race(
        2026,
        2,
        "Late race",
        "",
        "",
        "",
        (Session("race", datetime(2026, 3, 15, 23, tzinfo=timezone.utc)),),
    )
    state["payload"] = [
        {
            "session_key": 13,
            "session_name": "Race",
            "date_start": "2026-03-16T00:05:00Z",
        }
    ]
    assert (await client.openf1_session(race, "race"))["session_key"] == 13


async def test_openf1_null_qualifying_arrays_are_renderable(client, monkeypatch):
    race = Race(
        2026,
        2,
        "China",
        "shanghai",
        "Shanghai",
        "China",
        (
            Session("race", datetime(2026, 3, 15, 7, tzinfo=timezone.utc)),
            Session("sprint_qualifying", datetime(2026, 3, 13, 7, tzinfo=timezone.utc)),
        ),
    )

    async def payload(base, path, params=None, ttl=30):
        if path == "/sessions":
            return [
                {
                    "session_key": 10,
                    "session_name": "Sprint Qualifying",
                    "date_start": "2026-03-13T07:00:00Z",
                }
            ]
        if path == "/session_result":
            return [
                {"driver_number": 4, "position": 1, "duration": [90.123, None, 88.5]},
                {"driver_number": 1, "position": None, "duration": None, "dsq": True},
            ]
        return [
            {"driver_number": 4, "full_name": "Lando Norris", "team_name": "McLaren"}
        ]

    monkeypatch.setattr(client, "_get", payload)
    result = await client.results(race, "sprint_qualifying")
    assert result.rows[0][-1] == "1:28.500"
    assert result.rows[1][-1] == "DSQ"


async def test_provider_500_is_not_cached(client, server):
    base, state = server
    state["status"] = 500
    with pytest.raises(api.DataError):
        await client._get(base, "/broken")
    assert not client._cache


async def test_schedule_uses_confirmed_session_end_when_available(client, monkeypatch):
    schedule = {
        "MRData": {
            "RaceTable": {
                "Races": [
                    {
                        "season": "2026",
                        "round": "2",
                        "raceName": "China",
                        "date": "2026-03-15",
                        "time": "07:00:00Z",
                    }
                ]
            }
        }
    }

    async def payload(base, path, params=None, ttl=30):
        if path == "/sessions":
            return [
                {
                    "session_name": "Race",
                    "date_start": "2026-03-15T07:05:00Z",
                    "date_end": "2026-03-15T09:05:00Z",
                }
            ]
        return schedule

    monkeypatch.setattr(client, "_get", payload)
    race = (await client.schedule(2026))[0]
    assert race.start == datetime(2026, 3, 15, 7, 5, tzinfo=timezone.utc)
    assert race.sessions[0].end == datetime(2026, 3, 15, 9, 5, tzinfo=timezone.utc)


async def test_openf1_outage_does_not_hide_jolpica_schedule(client, monkeypatch):
    async def payload(base, path, params=None, ttl=30):
        if path == "/sessions":
            raise api.DataError("unavailable")
        return {
            "MRData": {
                "RaceTable": {
                    "Races": [
                        {
                            "season": "2026",
                            "round": "2",
                            "raceName": "China",
                            "date": "2026-03-15",
                            "time": "07:00:00Z",
                        }
                    ]
                }
            }
        }

    monkeypatch.setattr(client, "_get", payload)
    assert (await client.schedule(2026))[0].round == 2
