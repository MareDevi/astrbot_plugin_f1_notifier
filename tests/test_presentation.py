from __future__ import annotations

import asyncio
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
from PIL import Image

from src.astrbot_plugin_f1_notifier.formatter import (
    format_table,
    schedule_table,
    weekend_table,
)
from src.astrbot_plugin_f1_notifier.image_renderer import ImageRenderer
from src.astrbot_plugin_f1_notifier.models import Race, Session, Table


def _sprint_race() -> Race:
    """Return a sprint weekend with deliberately unordered sessions."""
    utc = timezone.utc
    return Race(
        season=2025,
        round=3,
        name="中国大奖赛",
        circuit_id="shanghai",
        circuit_name="上海国际赛车场",
        country="中国",
        sessions=(
            Session("race", datetime(2025, 3, 16, 4, 0, tzinfo=utc)),
            Session("sprint", datetime(2025, 3, 15, 4, 0, tzinfo=utc)),
            Session("practice1", datetime(2025, 3, 14, 1, 30, tzinfo=utc)),
            Session("qualifying", datetime(2025, 3, 15, 7, 0, tzinfo=utc)),
            Session("practice2", datetime(2025, 3, 14, 5, 30, tzinfo=utc)),
            Session("sprint_qualifying", datetime(2025, 3, 14, 7, 30, tzinfo=utc)),
            Session("practice3", datetime(2025, 3, 15, 2, 30, tzinfo=utc)),
        ),
    )


def test_format_table_preserves_card_content_in_reading_order() -> None:
    table = Table(
        title="F1 车手积分榜",
        subtitle="2025 赛季",
        columns=("名次", "车手", "积分"),
        rows=(("1", "奥斯卡·皮亚斯特里", "228"), ("2", "兰多·诺里斯", "201")),
        footer="更新时间：2025-07-01 12:00",
    )

    rendered = format_table(table)

    expected_parts = [
        "F1 车手积分榜",
        "2025 赛季",
        "名次  |  车手  |  积分",
        "1  |  奥斯卡·皮亚斯特里  |  228",
        "2  |  兰多·诺里斯  |  201",
        "更新时间：2025-07-01 12:00",
    ]
    positions = [rendered.index(part) for part in expected_parts]
    assert positions == sorted(positions)


def test_schedule_table_converts_every_session_to_requested_timezone() -> None:
    table = schedule_table([_sprint_race()], timezone="Asia/Shanghai")

    assert table.subtitle == "时区：Asia/Shanghai"
    assert table.columns == ("开始时间", "分站", "赛段", "UTC 偏移")
    assert len(table.rows) == 7
    assert [row[2] for row in table.rows] == [
        "练习赛 FP1",
        "练习赛 FP2",
        "冲刺排位赛",
        "练习赛 FP3",
        "冲刺赛",
        "排位赛",
        "正赛",
    ]
    assert [row[0] for row in table.rows] == [
        "2025-03-14 09:30",
        "2025-03-14 13:30",
        "2025-03-14 15:30",
        "2025-03-15 10:30",
        "2025-03-15 12:00",
        "2025-03-15 15:00",
        "2025-03-16 12:00",
    ]
    assert {row[3] for row in table.rows} == {"UTC+08:00"}


def test_weekend_table_labels_dst_offset_and_preserves_circuit() -> None:
    utc = timezone.utc
    race = Race(
        season=2025,
        round=2,
        name="迈阿密大奖赛",
        circuit_id="miami",
        circuit_name="迈阿密国际赛车场",
        country="美国",
        sessions=(
            Session(
                "race",
                datetime(2025, 5, 4, 20, 0, tzinfo=utc),
                datetime(2025, 5, 4, 22, 0, tzinfo=utc),
            ),
        ),
    )

    table = weekend_table(race, timezone="America/New_York")

    assert table.title == "F1 第2站 · 迈阿密大奖赛"
    assert "America/New_York" in table.subtitle
    assert table.circuit_id == "miami"
    assert table.rows == (
        (
            "2025-05-04 16:00",
            "正赛",
            "2025-05-04 18:00",
            "UTC-04:00",
        ),
    )


async def test_renderer_writes_cached_1200px_png(tmp_path: Path) -> None:
    data_dir = tmp_path / "cards"
    renderer = ImageRenderer(data_dir, max_age_minutes=30, max_count=10)
    table = Table(
        title="F1 冲刺周末时间表",
        subtitle="中国大奖赛 · Asia/Shanghai",
        columns=("开始时间", "赛段", "UTC 偏移"),
        rows=(
            ("03-14 09:30", "练习赛 FP1", "UTC+08:00"),
            ("03-14 13:30", "练习赛 FP2", "UTC+08:00"),
            ("03-14 15:30", "冲刺排位赛", "UTC+08:00"),
            ("03-15 12:00", "冲刺赛", "UTC+08:00"),
            ("03-16 12:00", "正赛", "UTC+08:00"),
        ),
        footer="赛程可能调整，请以赛事官方公告为准",
        circuit_id="shanghai",
    )

    try:
        first = await renderer.render(table)
        second = await renderer.render(table)
    finally:
        await renderer.close()

    assert first == second
    assert first.parent == data_dir
    assert first.suffix == ".png"
    with Image.open(first) as image:
        assert image.format == "PNG"
        assert image.size[0] == 1200
        assert 500 <= image.size[1] <= 900


async def test_cancelled_render_keeps_worker_until_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered = threading.Event()
    release = threading.Event()
    renderer = ImageRenderer(tmp_path, max_age_minutes=30, max_count=10)
    original_render_sync = renderer._render_sync

    def blocked_render_sync(table: Table) -> Path:
        """Wait until the test explicitly releases the worker."""
        entered.set()
        release.wait(timeout=2)
        return original_render_sync(table)

    monkeypatch.setattr(renderer, "_render_sync", blocked_render_sync)
    table = Table(
        title="取消测试",
        columns=("项目", "状态"),
        rows=(("渲染器", "正在工作"),),
    )
    render_task = asyncio.create_task(renderer.render(table))
    for _ in range(100):
        if entered.is_set():
            break
        await asyncio.sleep(0.01)
    assert entered.is_set()

    try:
        render_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await render_task

        queued_task = asyncio.create_task(renderer.render(table))
        close_task = asyncio.create_task(renderer.close())
        await asyncio.sleep(0.05)
        assert not close_task.done()
        assert not queued_task.done()
    finally:
        release.set()

    for _ in range(200):
        if close_task.done():
            break
        await asyncio.sleep(0.01)
    assert close_task.done()
    close_task.result()
    with pytest.raises(RuntimeError, match="closed"):
        await queued_task
    assert list(tmp_path.glob("f1-card-*.png"))


def test_circuit_mapping_is_allowlisted_and_numeric(tmp_path: Path) -> None:
    renderer = ImageRenderer(tmp_path)
    assert renderer._circuit_svg_path("albert_park").name == "melbourne-2.svg"
    assert renderer._circuit_svg_path("villeneuve").name == "montreal-6.svg"
    assert renderer._circuit_svg_path("americas").name == "austin-1.svg"
    assert renderer._circuit_svg_path("../melbourne") is None
    assert renderer._circuit_svg_path("unknown") is None

    circuits_dir = tmp_path / "circuits"
    circuits_dir.mkdir()
    (circuits_dir / "shanghai-2.svg").write_text("<svg/>", encoding="utf-8")
    (circuits_dir / "shanghai-10.svg").write_text("<svg/>", encoding="utf-8")
    selected = renderer._circuit_svg_path("shanghai", circuits_dir=circuits_dir)
    assert selected.name == "shanghai-10.svg"


async def test_renderer_sizes_many_long_rows_without_truncating_table(
    tmp_path: Path,
) -> None:
    rows = tuple(
        (
            str(index),
            f"车手{index} · Alexander Very-Long-Family-Name-{index}",
            "车队名称同样很长 Very Long Constructor Name",
            str(300 - index),
        )
        for index in range(1, 31)
    )
    table = Table(
        title="F1 车手积分榜",
        subtitle="30 位车手完整排名",
        columns=("名次", "车手", "车队", "积分"),
        rows=rows,
        footer="完整榜单",
        circuit_id="unknown",
    )
    renderer = ImageRenderer(tmp_path, max_count=5)
    try:
        path = await renderer.render(table)
    finally:
        await renderer.close()

    with Image.open(path) as image:
        assert image.width == 1200
        assert image.height >= 1450


async def test_cache_cleanup_removes_old_and_excess_cards(tmp_path: Path) -> None:
    stale_a = tmp_path / "f1-card-aaaaaaaa.png"
    stale_b = tmp_path / "f1-card-bbbbbbbb.png"
    strict_retained = tmp_path / "f1-card-cccccccc.png"
    unrelated = tmp_path / "keep-me.png"
    for path in (stale_a, stale_b, strict_retained, unrelated):
        Image.new("RGB", (10, 10), "red").save(path)
    old_time = time.time() - 3600
    for path in (stale_a, stale_b, strict_retained):
        path.touch()
        os.utime(path, (old_time, old_time))

    renderer = ImageRenderer(
        tmp_path,
        max_age_minutes=1,
        max_count=2,
    )
    try:
        current = await renderer.render(
            Table(title="缓存清理", rows=(("状态", "正常"),))
        )
    finally:
        await renderer.close()

    generated = list(tmp_path.glob("f1-card-*.png"))
    assert current in generated
    assert len(generated) <= 2
    assert stale_a not in generated
    assert stale_b not in generated
    assert strict_retained not in generated
    assert unrelated.exists()


async def test_pruning_counts_reused_cache_in_new_limit(tmp_path: Path) -> None:
    table = Table(title="复用缓存", rows=(("状态", "有效"),))
    initial = ImageRenderer(tmp_path, max_count=10)
    try:
        current = await initial.render(table)
    finally:
        await initial.close()

    newer: list[Path] = []
    for index in range(3):
        path = tmp_path / f"f1-card-newer{index}.png"
        Image.new("RGB", (10, 10), "red").save(path)
        newer.append(path)

    lowered = ImageRenderer(tmp_path, max_count=2)
    try:
        reused = await lowered.render(table)
    finally:
        await lowered.close()

    generated = list(tmp_path.glob("f1-card-*.png"))
    assert reused == current
    assert current in generated
    assert len(generated) == 2
    assert sum(path in generated for path in newer) == 1
