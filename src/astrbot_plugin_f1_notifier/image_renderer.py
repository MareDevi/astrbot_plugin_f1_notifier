"""Generate polished F1 presentation cards with Pillow."""

from __future__ import annotations

import asyncio
import hashlib
import io
import os
import tempfile
import time
from pathlib import Path
from types import MappingProxyType

from PIL import Image, ImageDraw, ImageFont

try:
    import cairosvg
except (ImportError, OSError):
    cairosvg = None

from .models import Table

_LAYOUT_VERSION = "f1-card-v1"
_PLUGIN_ROOT = Path(__file__).resolve().parents[2]
_ASSETS_DIR = _PLUGIN_ROOT / "assets"
_FONTS_DIR = _ASSETS_DIR / "fonts"
_CARD_WIDTH = 1200
_MARGIN = 56
_CACHE_PREFIX = "f1-card-"
_CIRCUIT_SVG_MAP = MappingProxyType(
    {
        "albert_park": "melbourne",
        "shanghai": "shanghai",
        "suzuka": "suzuka",
        "bahrain": "bahrain",
        "jeddah": "jeddah",
        "miami": "miami",
        "imola": "imola",
        "monaco": "monaco",
        "catalunya": "catalunya",
        "villeneuve": "montreal",
        "red_bull_ring": "spielberg",
        "silverstone": "silverstone",
        "spa": "spa-francorchamps",
        "hungaroring": "hungaroring",
        "zandvoort": "zandvoort",
        "monza": "monza",
        "baku": "baku",
        "marina_bay": "marina-bay",
        "americas": "austin",
        "rodriguez": "mexico-city",
        "interlagos": "interlagos",
        "vegas": "las-vegas",
        "losail": "lusail",
        "yas_marina": "yas-marina",
        "madring": "madring",
        "portimao": "portimao",
        "istanbul": "istanbul",
        "mugello": "mugello",
        "sochi": "sochi",
        "nurburgring": "nurburgring",
        "hockenheimring": "hockenheimring",
        "sepang": "sepang",
        "yeongam": "yeongam",
        "buddh": "buddh",
    }
)
_CJK_FONTS = {
    False: (
        Path("/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
        Path("/System/Library/Fonts/PingFang.ttc"),
        Path("/System/Library/Fonts/Hiragino Sans GB.ttc"),
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/simhei.ttf"),
        Path("/usr/share/fonts/noto/NotoSans-Regular.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ),
    True: (
        Path("/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"),
        Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
        Path("/System/Library/Fonts/PingFang.ttc"),
        Path("/System/Library/Fonts/Hiragino Sans GB.ttc"),
        Path("C:/Windows/Fonts/msyhbd.ttc"),
        Path("C:/Windows/Fonts/simhei.ttf"),
        Path("/usr/share/fonts/noto/NotoSans-Bold.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ),
}


class ImageRenderer:
    """Render structured tables as cached F1 cards.

    Args:
        data_dir: Directory for generated PNG files.
        max_age_minutes: Maximum cache age in minutes.
        max_count: Maximum number of generated cards to retain.
    """

    def __init__(
        self,
        data_dir: Path,
        max_age_minutes: int = 30,
        max_count: int = 50,
    ) -> None:
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.max_age_minutes = max(0, int(max_age_minutes))
        self.max_count = max(1, int(max_count))
        self._semaphore = asyncio.Semaphore(1)
        self._state_lock = asyncio.Lock()
        self._workers: dict[asyncio.Task[Path], asyncio.Event] = {}
        self._font_cache: dict[tuple[int, bool], ImageFont.FreeTypeFont] = {}
        self._closed = False

    async def render(self, table: Table) -> Path:
        """Render a table to a PNG card.

        Args:
            table: Structured content to draw.

        Returns:
            Path to the generated or cached PNG file.

        Raises:
            RuntimeError: If the renderer has already been closed.
        """
        await self._semaphore.acquire()
        task: asyncio.Task[Path] | None = None
        try:
            async with self._state_lock:
                if self._closed:
                    raise RuntimeError("ImageRenderer is closed")
                task = asyncio.create_task(asyncio.to_thread(self._render_sync, table))
                event = asyncio.Event()
                self._workers[task] = event

                def worker_finished(done: asyncio.Task[Path]) -> None:
                    try:
                        done.result()
                    except (asyncio.CancelledError, Exception):
                        pass
                    self._workers.pop(done, None)
                    event.set()
                    self._semaphore.release()

                task.add_done_callback(worker_finished)
        except BaseException:
            if task is None:
                self._semaphore.release()
            raise

        await asyncio.shield(event.wait())
        return task.result()

    async def close(self) -> None:
        """Close the renderer after all in-flight card work finishes."""
        async with self._state_lock:
            self._closed = True
            events = tuple(self._workers.values())
        if events:
            await asyncio.gather(*(event.wait() for event in events))

    def _load_font(self, size: int, *, bold: bool = False) -> ImageFont.ImageFont:
        """Load the first available CJK-capable font.

        Args:
            size: Requested font size in pixels.
            bold: Whether to prefer a bold face.

        Returns:
            A Pillow font object. A bundled font or Pillow's default is used
            when no system CJK font is installed.
        """
        key = (size, bold)
        if key in self._font_cache:
            return self._font_cache[key]

        bundled = _FONTS_DIR / ("Orbitron-Bold.ttf" if bold else "Orbitron-Regular.ttf")
        for path in (*_CJK_FONTS[bold], bundled):
            try:
                font = ImageFont.truetype(str(path), size)
            except (OSError, ValueError):
                continue
            self._font_cache[key] = font
            return font

        try:
            return ImageFont.load_default(size=size)
        except TypeError:
            return ImageFont.load_default()

    def _ellipsize(
        self,
        text: str,
        font: ImageFont.ImageFont,
        max_width: float,
    ) -> str:
        """Trim text to a measured width with a trailing ellipsis.

        Args:
            text: Text to measure.
            font: Font used for drawing.
            max_width: Available horizontal space.

        Returns:
            Text that fits the requested width.
        """
        if not text:
            return ""
        measure = ImageDraw.Draw(Image.new("RGB", (1, 1))).textlength
        if measure(text, font=font) <= max_width:
            return text
        suffix = "…"
        while text and measure(text + suffix, font=font) > max_width:
            text = text[:-1]
        return text + suffix

    def _wrap(
        self,
        text: str,
        font: ImageFont.ImageFont,
        max_width: float,
        max_lines: int = 3,
    ) -> list[str]:
        """Wrap text by measured glyph width without exceeding line limits.

        Args:
            text: Text to wrap.
            font: Font used for drawing.
            max_width: Available width for each line.
            max_lines: Maximum number of lines to return.

        Returns:
            Wrapped lines. Overflow is replaced by an ellipsis on the last line.
        """
        text = " ".join(str(text).splitlines()).strip()
        if not text:
            return [""]

        measure = ImageDraw.Draw(Image.new("RGB", (1, 1))).textlength
        lines: list[str] = []
        remaining = text
        while remaining and len(lines) < max_lines:
            candidate = ""
            split_at = 0
            for index, character in enumerate(remaining):
                trial = candidate + character
                if candidate and measure(trial, font=font) > max_width:
                    break
                candidate = trial
                if character in (" ", "/", "-", "·"):
                    split_at = index + 1
            if candidate == remaining:
                lines.append(candidate)
                return lines
            if split_at and split_at < len(candidate):
                lines.append(candidate[:split_at].rstrip())
                remaining = remaining[split_at:].lstrip()
            else:
                lines.append(candidate.rstrip())
                remaining = remaining[len(candidate) :].lstrip()

        if remaining:
            lines[-1] = self._ellipsize(f"{lines[-1]}…{remaining}", font, max_width)
        return lines

    def _circuit_svg_path(
        self, circuit_id: str, circuits_dir: Path | None = None
    ) -> Path | None:
        """Resolve an allowlisted circuit ID to its newest layout SVG.

        Args:
            circuit_id: External circuit identifier from the source API.
            circuits_dir: Optional circuit directory override for testing.

        Returns:
            The highest-numbered matching SVG, or ``None`` when the identifier
            is unknown or has no bundled layout.
        """
        stem = _CIRCUIT_SVG_MAP.get(str(circuit_id).strip().lower())
        if stem is None:
            return None
        directory = circuits_dir or (_ASSETS_DIR / "circuits")
        variants: list[tuple[int, Path]] = []
        for path in directory.glob(f"{stem}-*.svg"):
            suffix = path.stem.removeprefix(f"{stem}-")
            if suffix.isdigit():
                variants.append((int(suffix), path))
        return max(variants, default=(0, None))[1]

    def _circuit_graphic(
        self, circuit_id: str, size: tuple[int, int]
    ) -> Image.Image | None:
        """Render a bundled circuit SVG when CairoSVG is available.

        Args:
            circuit_id: Circuit identifier from the table.
            size: Requested image size.

        Returns:
            An RGBA circuit image, or ``None`` when no genuine asset can be
            rendered.
        """
        width, height = size
        if circuit_id and cairosvg is not None:
            svg_path = self._circuit_svg_path(circuit_id)
            if svg_path is not None:
                output = io.BytesIO()
                try:
                    cairosvg.svg2png(
                        url=str(svg_path),
                        write_to=output,
                        output_width=width,
                        output_height=height,
                    )
                    output.seek(0)
                    with Image.open(output) as source:
                        return source.convert("RGBA")
                except Exception:
                    # CairoSVG can surface backend-specific parse exceptions.
                    pass
        return None

    def _cache_path(self, table: Table) -> Path:
        """Compute a deterministic cache path for a table.

        Args:
            table: Structured table content.

        Returns:
            Stable PNG path derived from every rendered field.
        """
        parts = [
            _LAYOUT_VERSION,
            table.title,
            table.subtitle,
            "\x1e".join(table.columns),
            "\x1f".join("\x1e".join(row) for row in table.rows),
            table.footer,
            table.circuit_id,
        ]
        digest = hashlib.sha256("\x1d".join(parts).encode("utf-8")).hexdigest()[:24]
        return self.data_dir / f"{_CACHE_PREFIX}{digest}.png"

    def _prune_cache(self, keep: Path) -> None:
        """Remove stale and excess generated cards.

        Args:
            keep: The card produced by the current render. It is never removed
                by this cleanup pass.
        """
        try:
            entries = sorted(
                self.data_dir.glob(f"{_CACHE_PREFIX}*.png"),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
        except OSError:
            return

        cutoff = time.time() - self.max_age_minutes * 60
        retained_others = 0
        for path in entries:
            if path == keep:
                continue
            try:
                too_old = path.stat().st_mtime < cutoff
                too_many = retained_others >= self.max_count - 1
                if too_old or too_many:
                    path.unlink(missing_ok=True)
                else:
                    retained_others += 1
            except OSError:
                continue

    def _render_sync(self, table: Table) -> Path:
        """Render one table in a worker thread.

        Args:
            table: Structured content to draw.

        Returns:
            Path to the generated or cached PNG file.
        """
        self.data_dir.mkdir(parents=True, exist_ok=True)
        output_path = self._cache_path(table)
        cache_fresh = False
        try:
            age = time.time() - output_path.stat().st_mtime
            cache_fresh = age <= self.max_age_minutes * 60
        except OSError:
            pass

        if not cache_fresh:
            image = self._draw_card(table)
            temporary_path: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    dir=self.data_dir,
                    prefix=".f1-card-",
                    suffix=".tmp",
                    delete=False,
                ) as temporary:
                    temporary_path = Path(temporary.name)
                image.save(temporary_path, format="PNG", optimize=True)
                os.replace(temporary_path, output_path)
            finally:
                if temporary_path is not None:
                    temporary_path.unlink(missing_ok=True)

        self._prune_cache(output_path)
        return output_path

    def _draw_card(self, table: Table) -> Image.Image:
        """Draw the complete card image.

        Args:
            table: Structured content to draw.

        Returns:
            A 1200-pixel-wide RGB card image.
        """
        table_width = _CARD_WIDTH - _MARGIN * 2
        regular = self._load_font(18)
        bold = self._load_font(19, bold=True)
        header_font = self._load_font(17, bold=True)
        title_font = self._load_font(36, bold=True)
        subtitle_font = self._load_font(19)
        footer_font = self._load_font(17)

        column_count = max(
            len(table.columns),
            max((len(row) for row in table.rows), default=0),
            1,
        )
        columns = tuple(table.columns) + ("",) * (column_count - len(table.columns))
        normalized_rows = tuple(
            tuple(row) + ("",) * (column_count - len(row)) for row in table.rows
        )

        measure = ImageDraw.Draw(Image.new("RGB", (1, 1))).textlength
        gap = 18
        usable_width = table_width - gap * (column_count - 1)
        natural_widths = []
        for index, heading in enumerate(columns):
            cells = [heading, *(row[index] for row in normalized_rows)]
            widest = max((measure(cell, font=regular) for cell in cells), default=0)
            natural_widths.append(min(max(76.0, widest + 24), 360.0))
        width_total = sum(natural_widths)
        widths = [
            max(76, int(usable_width * width_value / width_total))
            for width_value in natural_widths
        ]
        widths[-1] += usable_width - sum(widths)

        wrapped_rows: list[list[list[str]]] = []
        row_heights: list[int] = []
        line_height = 25
        for row in normalized_rows:
            wrapped_row = [
                self._wrap(row[index], regular, widths[index] - 14)
                for index in range(column_count)
            ]
            wrapped_rows.append(wrapped_row)
            row_heights.append(
                22 + max(len(lines) for lines in wrapped_row) * line_height
            )

        header_height = 188
        column_header_height = 54
        footer_height = 88
        table_height = sum(row_heights)
        card_height = (
            header_height + column_header_height + table_height + footer_height + 20
        )

        image = Image.new("RGB", (_CARD_WIDTH, card_height), "#090C12")
        draw = ImageDraw.Draw(image)
        for y in range(card_height):
            blend = y / max(1, card_height)
            color = (
                int(9 + 12 * blend),
                int(12 + 12 * blend),
                int(18 + 15 * blend),
            )
            draw.line((0, y, _CARD_WIDTH, y), fill=color)

        draw.rectangle((0, 0, _CARD_WIDTH, 9), fill="#E1062C")
        draw.rectangle((_MARGIN, 32, _MARGIN + 8, 146), fill="#E1062C")
        draw.rounded_rectangle(
            (_MARGIN + 28, 38, _MARGIN + 132, 142),
            radius=18,
            fill="#E1062C",
        )
        draw.text(
            (_MARGIN + 47, 63),
            "F1",
            font=self._load_font(42, bold=True),
            fill="#FFFFFF",
        )
        title = self._ellipsize(table.title or "F1 信息", title_font, 620)
        draw.text((_MARGIN + 158, 50), title, font=title_font, fill="#FFFFFF")
        subtitle = self._ellipsize(table.subtitle, subtitle_font, 620)
        if subtitle:
            draw.text(
                (_MARGIN + 160, 104),
                subtitle,
                font=subtitle_font,
                fill="#AEB6C5",
            )
        draw.text(
            (_MARGIN + 160, 139),
            "F1 赛事信息卡",
            font=self._load_font(13, bold=True),
            fill="#E1062C",
        )

        graphic = self._circuit_graphic(table.circuit_id, (240, 132))
        graphic_x = _CARD_WIDTH - _MARGIN - 240
        if graphic is not None:
            image.paste(graphic, (graphic_x, 34), graphic)
        else:
            draw.rounded_rectangle(
                (graphic_x, 34, graphic_x + 240, 166),
                radius=20,
                outline="#4A1020",
                width=2,
            )
            draw.ellipse(
                (graphic_x + 77, 49, graphic_x + 163, 135),
                outline="#E1062C",
                width=4,
            )
            draw.text(
                (graphic_x + 92, 70),
                "F1",
                font=self._load_font(34, bold=True),
                fill="#FFFFFF",
            )
            draw.text(
                (graphic_x + 92, 139),
                "赛道图",
                font=self._load_font(14, bold=True),
                fill="#AEB6C5",
            )

        header_top = header_height
        draw.rounded_rectangle(
            (
                _MARGIN,
                header_top,
                _CARD_WIDTH - _MARGIN,
                header_top + column_header_height,
            ),
            radius=12,
            fill="#202532",
        )
        x = _MARGIN + 10
        for index, heading in enumerate(columns):
            label = self._ellipsize(heading, header_font, widths[index] - 14)
            draw.text((x, header_top + 16), label, font=header_font, fill="#FFFFFF")
            x += widths[index] + gap

        y = header_top + column_header_height
        for row_index, (wrapped_row, row_height) in enumerate(
            zip(wrapped_rows, row_heights, strict=True)
        ):
            background = "#111620" if row_index % 2 == 0 else "#151B27"
            draw.rounded_rectangle(
                (_MARGIN, y + 2, _CARD_WIDTH - _MARGIN, y + row_height - 2),
                radius=8,
                fill=background,
            )
            x = _MARGIN + 10
            for column_index, lines in enumerate(wrapped_row):
                text_color = "#F2F5FA"
                if column_index == 0 and row_index < 3:
                    text_color = "#FF4058"
                for line_index, line in enumerate(lines):
                    draw.text(
                        (x, y + 10 + line_index * line_height),
                        line,
                        font=bold if column_index == 0 else regular,
                        fill=text_color,
                    )
                x += widths[column_index] + gap
            y += row_height

        footer_top = card_height - footer_height
        draw.line(
            (_MARGIN, footer_top, _CARD_WIDTH - _MARGIN, footer_top),
            fill="#3A4050",
            width=2,
        )
        footer = self._ellipsize(table.footer, footer_font, table_width - 180)
        draw.text((_MARGIN, footer_top + 27), footer, font=footer_font, fill="#AEB6C5")
        draw.ellipse(
            (
                _CARD_WIDTH - _MARGIN - 14,
                footer_top + 31,
                _CARD_WIDTH - _MARGIN,
                footer_top + 45,
            ),
            fill="#E1062C",
        )
        return image
