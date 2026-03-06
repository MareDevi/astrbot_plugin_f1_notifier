"""Pillow-based image renderer for F1 plugin.

Generates broadcast-style F1 graphics using Pillow + CairoSVG.
Uses:
  - Local Orbitron TTF fonts for the racing aesthetic
  - Local circuit SVG files rendered via CairoSVG
  - Team colours mapped from constructor names

Each public ``render_*`` function returns a **file path** (str) pointing
to a temporary PNG. The caller passes the path directly to
``event.image_result(path)`` or ``Image.fromFileSystem(path)``.
"""

from __future__ import annotations

import io
import tempfile
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import cairosvg
from PIL import Image, ImageDraw, ImageFont

from .models import (
    JolpicaConstructorStanding,
    JolpicaDriverStanding,
    JolpicaRace,
    JolpicaSessionSchedule,
    OpenF1Driver,
    OpenF1Position,
    OpenF1Session,
    OpenF1SessionResult,
)

# ── Paths ──────────────────────────────────────────────────────────────────────

_ASSETS_DIR = Path(__file__).resolve().parent.parent.parent / "assets"
_FONTS_DIR = _ASSETS_DIR / "fonts"
_CIRCUITS_DIR = _ASSETS_DIR / "circuits"

# Flags stored under the AstrBot data directory for persistence across updates
try:
    from astrbot.core.utils.astrbot_path import get_astrbot_data_path

    _FLAGS_DIR = (
        Path(get_astrbot_data_path())
        / "plugin_data"
        / "astrbot_plugin_f1_notifier"
        / "flags"
    )
except Exception:
    _FLAGS_DIR = _ASSETS_DIR / "flags"

# ── Constants ──────────────────────────────────────────────────────────────────

CARD_W = 700
HEADER_H = 80
ROW_H = 56
FOOTER_H = 36
ROW_MARGIN_X = 12
ROW_MARGIN_Y = 4
ROW_RADIUS = 10
TEAM_BAR_W = 4

# Colours
BG_TOP = (21, 21, 30)  # #15151E
BG_BOT = (26, 26, 46)  # #1A1A2E
HEADER_RED = (225, 6, 0)  # #E10600
WHITE = (255, 255, 255)
GOLD = (255, 215, 0)
SILVER = (192, 192, 192)
BRONZE = (205, 127, 50)
RED_ACCENT = (225, 6, 0)

CST = timezone(timedelta(hours=8))

# ── Team colours ───────────────────────────────────────────────────────────────

TEAM_COLOURS: dict[str, tuple[int, int, int]] = {
    "red bull": (54, 113, 198),
    "ferrari": (232, 0, 45),
    "mclaren": (255, 128, 0),
    "mercedes": (39, 244, 210),
    "aston martin": (34, 153, 113),
    "alpine": (0, 147, 204),
    "williams": (100, 196, 255),
    "racing bulls": (102, 146, 255),
    "rb": (102, 146, 255),
    "haas": (182, 186, 189),
    "sauber": (82, 226, 82),
    "audi": (255, 0, 0),
    "cadillac": (144, 144, 144),
}

# Jolpica circuit_id → SVG filename stem (julesr0y/f1-circuits-svg)
CIRCUIT_SVG_MAP: dict[str, str] = {
    "albert_park": "melbourne-2",
    "shanghai": "shanghai-1",
    "suzuka": "suzuka-2",
    "bahrain": "bahrain-1",
    "jeddah": "jeddah-1",
    "miami": "miami-1",
    "imola": "imola-3",
    "monaco": "monaco-6",
    "catalunya": "catalunya-6",
    "villeneuve": "montreal-6",
    "red_bull_ring": "spielberg-3",
    "silverstone": "silverstone-8",
    "spa": "spa-francorchamps-4",
    "hungaroring": "hungaroring-3",
    "zandvoort": "zandvoort-5",
    "monza": "monza-7",
    "baku": "baku-1",
    "marina_bay": "marina-bay-4",
    "americas": "austin-1",
    "rodriguez": "mexico-city-3",
    "interlagos": "interlagos-2",
    "vegas": "las-vegas-1",
    "losail": "lusail-1",
    "yas_marina": "yas-marina-2",
    "madring": "madring-1",
    "portimao": "portimao-1",
    "istanbul": "istanbul-1",
    "mugello": "mugello-1",
    "sochi": "sochi-1",
    "nurburgring": "nurburgring-4",
    "hockenheimring": "hockenheimring-4",
    "sepang": "sepang-1",
    "yeongam": "yeongam-1",
    "buddh": "buddh-1",
}

FLAG_MAP: dict[str, str] = {
    "Australia": "AU",
    "China": "CN",
    "Japan": "JP",
    "Bahrain": "BH",
    "Saudi Arabia": "SA",
    "USA": "US",
    "United States": "US",
    "Canada": "CA",
    "Monaco": "MC",
    "Spain": "ES",
    "Austria": "AT",
    "UK": "GB",
    "United Kingdom": "GB",
    "Belgium": "BE",
    "Hungary": "HU",
    "Netherlands": "NL",
    "Italy": "IT",
    "Azerbaijan": "AZ",
    "Singapore": "SG",
    "Mexico": "MX",
    "Brazil": "BR",
    "UAE": "AE",
    "United Arab Emirates": "AE",
    "Qatar": "QA",
}

# ── Font loading ───────────────────────────────────────────────────────────────

_font_cache: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}


def _font(weight: str = "Bold", size: int = 14) -> ImageFont.FreeTypeFont:
    """Load an Orbitron font variant, cached."""
    key = (weight, size)
    if key not in _font_cache:
        path = _FONTS_DIR / f"Orbitron-{weight}.ttf"
        if not path.exists():
            path = _FONTS_DIR / "Orbitron-Regular.ttf"
        _font_cache[key] = ImageFont.truetype(str(path), size)
    return _font_cache[key]


# ── Helpers ────────────────────────────────────────────────────────────────────


def _flag(country: str) -> str:
    return FLAG_MAP.get(country, "")


def _cc_to_twemoji_stem(code: str) -> str:
    """Convert 2-letter country code to twemoji filename stem, e.g. 'AU' → '1f1e6-1f1fa'."""
    return "-".join(f"{0x1F1E6 + ord(c) - ord('A'):x}" for c in code.upper())


def _download_to_file(url: str, dest: Path, timeout: int = 5) -> bool:
    """Download a URL to a local file. Returns True on success."""
    if not url.startswith(("http://", "https://")):
        return False
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "AstrBot/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(resp.read())
        return True
    except Exception:
        return False


def _load_flag_image(country: str, size: int = 20) -> Image.Image | None:
    """Load a flag image (twemoji SVG) for a country name."""
    code = FLAG_MAP.get(country)
    if not code:
        return None
    stem = _cc_to_twemoji_stem(code)
    svg_path = _FLAGS_DIR / f"{stem}.svg"
    if not svg_path.exists():
        url = (
            f"https://cdn.jsdelivr.net/gh/twitter/twemoji@latest/assets/svg/{stem}.svg"
        )
        if not _download_to_file(url, svg_path):
            return None
    try:
        png_data = cairosvg.svg2png(
            url=str(svg_path), output_width=size, output_height=size
        )
        return Image.open(io.BytesIO(png_data)).convert("RGBA")
    except Exception:
        return None


_HEADSHOTS_CACHE: dict[str, Image.Image | None] = {}


def _make_circular(source: Image.Image, size: int) -> Image.Image:
    """Resize image to a circle of given size."""
    source = source.resize((size, size), Image.Resampling.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    result = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    result.paste(source, (0, 0), mask)
    return result


def _load_headshot(url: str | None, size: int = 32) -> Image.Image | None:
    """Download and cache driver headshot as a circular image."""
    if not url:
        return None
    if not url.startswith(("http://", "https://")):
        return None
    if url in _HEADSHOTS_CACHE:
        cached = _HEADSHOTS_CACHE[url]
        return cached.copy() if cached else None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "AstrBot/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310
            data = resp.read(1024 * 1024)  # Max 1MB
        raw = Image.open(io.BytesIO(data)).convert("RGBA")
        result = _make_circular(raw, size)
        _HEADSHOTS_CACHE[url] = result
        return result.copy()
    except Exception:
        _HEADSHOTS_CACHE[url] = None
        return None


def _draw_flagged_text(
    img: Image.Image,
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    country: str,
    text: str,
    font: ImageFont.FreeTypeFont,
    fill: tuple,
    flag_size: int = 20,
) -> None:
    """Draw an optional flag image followed by text."""
    x, y_pos = xy
    flag_img = _load_flag_image(country, flag_size)
    if flag_img is not None:
        flag_y = y_pos + max(0, (font.size - flag_size) // 2)
        img.paste(flag_img, (x, int(flag_y)), flag_img)
        x += flag_size + 6
    draw.text((x, y_pos), text, fill=fill, font=font)


def _team_colour(constructor_name: str) -> tuple[int, int, int]:
    name_lower = constructor_name.lower()
    for key, colour in TEAM_COLOURS.items():
        if key in name_lower:
            return colour
    return (255, 255, 255)


def _parse_openf1_team_colour(drv: OpenF1Driver) -> tuple[int, int, int] | None:
    """Parse hex team colour from OpenF1 driver data."""
    if not drv.team_colour:
        return None
    try:
        h = drv.team_colour.lstrip("#")
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    except (ValueError, IndexError):
        return None


def _utc_to_cst(date_str: str, time_str: str) -> str:
    try:
        dt_utc = datetime.strptime(
            f"{date_str}T{time_str.rstrip('Z')}", "%Y-%m-%dT%H:%M:%S"
        ).replace(tzinfo=timezone.utc)
        return dt_utc.astimezone(CST).strftime("%m-%d %H:%M")
    except ValueError:
        return f"{date_str} {time_str}"


def _session_cst(s: JolpicaSessionSchedule | None) -> str | None:
    if s is None:
        return None
    return _utc_to_cst(s.date, s.time)


def _format_lap_duration(seconds: float) -> str:
    if seconds <= 0:
        return "-"
    m = int(seconds) // 60
    s = seconds - m * 60
    return f"{m}:{s:06.3f}"


def _load_circuit_image(circuit_id: str, size: int = 80) -> Image.Image | None:
    """Load circuit SVG as a Pillow RGBA image."""
    layout_id = CIRCUIT_SVG_MAP.get(circuit_id, "")
    if not layout_id:
        return None
    svg_path = _CIRCUITS_DIR / f"{layout_id}.svg"
    if not svg_path.exists():
        return None
    try:
        png_data = cairosvg.svg2png(
            url=str(svg_path), output_width=size, output_height=size
        )
        return Image.open(io.BytesIO(png_data)).convert("RGBA")
    except Exception:
        return None


def _load_f1_logo(size: int = 50) -> Image.Image | None:
    """Load the F1 logo SVG from assets."""
    svg_path = _ASSETS_DIR / "f1.svg"
    if not svg_path.exists():
        return None
    try:
        png_data = cairosvg.svg2png(
            url=str(svg_path), output_width=size, output_height=size
        )
        return Image.open(io.BytesIO(png_data)).convert("RGBA")
    except Exception:
        return None


def _pos_colour(pos: int) -> tuple[int, int, int]:
    if pos == 1:
        return GOLD
    if pos == 2:
        return SILVER
    if pos == 3:
        return BRONZE
    return WHITE


_generated_files: list[tuple[float, str]] = []
_CLEANUP_MAX_AGE = 1800  # 30 minutes
_CLEANUP_INTERVAL = 20  # run cleanup every 20 saves
_save_counter = 0


def _cleanup_old_images() -> None:
    """Remove generated temp images older than _CLEANUP_MAX_AGE seconds."""
    import os
    import time

    now = time.time()
    remaining: list[tuple[float, str]] = []
    for ts, fpath in _generated_files:
        if now - ts > _CLEANUP_MAX_AGE:
            try:
                os.remove(fpath)
            except OSError:
                pass
        else:
            remaining.append((ts, fpath))
    _generated_files.clear()
    _generated_files.extend(remaining)


def _save_image(img: Image.Image) -> str:
    """Save image to a temp file and return the path. Old images are auto-cleaned."""
    global _save_counter
    import os
    import time

    fd, path = tempfile.mkstemp(suffix=".png")
    img.save(path, format="PNG", optimize=True)
    os.close(fd)

    _generated_files.append((time.time(), path))
    _save_counter += 1
    if _save_counter % _CLEANUP_INTERVAL == 0:
        _cleanup_old_images()

    return path


# ── Drawing primitives ─────────────────────────────────────────────────────────


def _create_card(
    n_rows: int,
    header_title: str,
    header_sub: str,
    circuit_id: str = "",
    extra_body_h: int = 0,
    country: str = "",
) -> tuple[Image.Image, ImageDraw.ImageDraw, int]:
    """Create the base card image with header. Returns (image, draw, y_cursor)."""
    body_h = n_rows * (ROW_H + ROW_MARGIN_Y) + extra_body_h + FOOTER_H + 16
    total_h = HEADER_H + body_h
    img = Image.new("RGBA", (CARD_W, total_h), BG_TOP)
    draw = ImageDraw.Draw(img)

    # Background bottom half
    draw.rectangle((0, HEADER_H, CARD_W, total_h), fill=BG_BOT)

    # Header bar
    draw.rectangle((0, 0, CARD_W, HEADER_H), fill=HEADER_RED)

    # Header text
    title_font = _font("ExtraBold", 18)
    sub_font = _font("Medium", 11)
    _draw_flagged_text(
        img, draw, (28, 18), country, header_title, title_font, WHITE, flag_size=22
    )
    draw.text((28, 46), header_sub, fill=(255, 255, 255, 220), font=sub_font)

    # F1 logo in header
    logo = _load_f1_logo(128)
    if logo is not None:
        logo_x = CARD_W - 28 - logo.width
        logo_y = (HEADER_H - logo.height) // 2
        img.paste(logo, (logo_x, logo_y), logo)

    # Circuit overlay in header (semi-transparent)
    circuit_img = _load_circuit_image(circuit_id, 56)
    if circuit_img is not None:
        alpha = circuit_img.split()[3].point(lambda p: int(p * 0.3))
        circuit_img.putalpha(alpha)
        cx = CARD_W - 180 - circuit_img.width
        cy = (HEADER_H - circuit_img.height) // 2
        img.paste(circuit_img, (cx, cy), circuit_img)

    y = HEADER_H + 8
    return img, draw, y


def _draw_footer(draw: ImageDraw.ImageDraw, img_h: int) -> None:
    """Draw the footer text."""
    footer_font = _font("Regular", 8)
    text = "F1 NOTIFIER · POWERED BY ASTRBOT"
    bbox = draw.textbbox((0, 0), text, font=footer_font)
    tw = bbox[2] - bbox[0]
    draw.text(
        ((CARD_W - tw) // 2, img_h - FOOTER_H + 8),
        text,
        fill=(255, 255, 255, 80),
        font=footer_font,
    )


def _draw_driver_row(
    img: Image.Image,
    draw: ImageDraw.ImageDraw,
    y: int,
    pos: int,
    driver_name: str,
    team_name: str,
    team_colour: tuple[int, int, int],
    acronym: str = "",
    stats: list[tuple[str, str]] | None = None,
    headshot_url: str | None = None,
) -> int:
    """Draw a single driver/result row. Returns new y position."""
    x0 = ROW_MARGIN_X
    x1 = CARD_W - ROW_MARGIN_X
    row_y0 = y
    row_y1 = y + ROW_H

    # Row background with podium highlight
    if pos == 1:
        bg = (255, 215, 0, 25)
    elif pos == 2:
        bg = (192, 192, 192, 20)
    elif pos == 3:
        bg = (205, 127, 50, 20)
    else:
        bg = (255, 255, 255, 15 if pos % 2 == 0 else 10)

    # Overlay for alpha blending
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ovr_draw = ImageDraw.Draw(overlay)
    ovr_draw.rounded_rectangle((x0, row_y0, x1, row_y1), radius=ROW_RADIUS, fill=bg)
    img.alpha_composite(overlay)

    # Team colour bar
    draw.rectangle((x0, row_y0 + 6, x0 + TEAM_BAR_W, row_y1 - 6), fill=team_colour)

    # Position number
    pos_font = _font("ExtraBold", 20)
    pos_col = _pos_colour(pos)
    draw.text(
        (x0 + 16, row_y0 + (ROW_H - 24) // 2), str(pos), fill=pos_col, font=pos_font
    )

    # Driver acronym circle or headshot
    circle_x = x0 + 56
    circle_y = row_y0 + (ROW_H - 36) // 2
    circle_r = 18

    headshot = _load_headshot(headshot_url, 32)
    if headshot is not None:
        # Team colour circle border
        draw.ellipse(
            (circle_x, circle_y, circle_x + circle_r * 2, circle_y + circle_r * 2),
            outline=team_colour,
            width=2,
        )
        # Paste circular headshot inside border
        img.paste(headshot, (circle_x + 2, circle_y + 2), headshot)
    else:
        draw.ellipse(
            (circle_x, circle_y, circle_x + circle_r * 2, circle_y + circle_r * 2),
            outline=team_colour,
            width=2,
        )
        acr_font = _font("Bold", 11)
        acr_text = acronym[:3].upper() if acronym else driver_name[:3].upper()
        acr_bbox = draw.textbbox((0, 0), acr_text, font=acr_font)
        acr_w = acr_bbox[2] - acr_bbox[0]
        acr_h = acr_bbox[3] - acr_bbox[1]
        draw.text(
            (circle_x + circle_r - acr_w // 2, circle_y + circle_r - acr_h // 2 - 1),
            acr_text,
            fill=WHITE,
            font=acr_font,
        )

    # Driver name + team
    name_x = circle_x + circle_r * 2 + 12
    name_font = _font("Bold", 13)
    team_font = _font("Regular", 10)
    draw.text((name_x, row_y0 + 8), driver_name, fill=WHITE, font=name_font)
    draw.text(
        (name_x, row_y0 + 26), team_name, fill=(255, 255, 255, 150), font=team_font
    )

    # Stats on the right side
    if stats:
        stat_x = x1 - 16
        label_font = _font("Regular", 8)
        value_font = _font("SemiBold", 12)
        for label, value in reversed(stats):
            vbbox = draw.textbbox((0, 0), value, font=value_font)
            lbbox = draw.textbbox((0, 0), label, font=label_font)
            col_w = max(vbbox[2] - vbbox[0], lbbox[2] - lbbox[0]) + 8
            draw.text(
                (stat_x - col_w, row_y0 + 8),
                label,
                fill=(255, 255, 255, 130),
                font=label_font,
            )
            val_col = RED_ACCENT if label == "PTS" and pos <= 3 else WHITE
            draw.text(
                (stat_x - col_w, row_y0 + 22),
                value,
                fill=val_col,
                font=value_font,
            )
            stat_x -= col_w + 16

    return row_y1 + ROW_MARGIN_Y


def _draw_constructor_row(
    img: Image.Image,
    draw: ImageDraw.ImageDraw,
    y: int,
    pos: int,
    constructor_name: str,
    team_colour: tuple[int, int, int],
    stats: list[tuple[str, str]] | None = None,
) -> int:
    """Draw a single constructor row. Returns new y position."""
    x0 = ROW_MARGIN_X
    x1 = CARD_W - ROW_MARGIN_X
    row_y0 = y
    row_y1 = y + ROW_H

    if pos == 1:
        bg = (255, 215, 0, 25)
    elif pos == 2:
        bg = (192, 192, 192, 20)
    elif pos == 3:
        bg = (205, 127, 50, 20)
    else:
        bg = (255, 255, 255, 15 if pos % 2 == 0 else 10)

    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ovr_draw = ImageDraw.Draw(overlay)
    ovr_draw.rounded_rectangle((x0, row_y0, x1, row_y1), radius=ROW_RADIUS, fill=bg)
    img.alpha_composite(overlay)

    draw.rectangle((x0, row_y0 + 6, x0 + TEAM_BAR_W, row_y1 - 6), fill=team_colour)

    # Position
    pos_font = _font("ExtraBold", 20)
    draw.text(
        (x0 + 16, row_y0 + (ROW_H - 24) // 2),
        str(pos),
        fill=_pos_colour(pos),
        font=pos_font,
    )

    # Constructor name
    name_font = _font("Bold", 14)
    draw.text(
        (x0 + 56, row_y0 + (ROW_H - 18) // 2),
        constructor_name,
        fill=WHITE,
        font=name_font,
    )

    # Stats
    if stats:
        stat_x = x1 - 16
        label_font = _font("Regular", 8)
        value_font = _font("SemiBold", 12)
        pts_font = _font("ExtraBold", 18)
        for label, value in reversed(stats):
            is_pts = label == "POINTS"
            vf = pts_font if is_pts else value_font
            vbbox = draw.textbbox((0, 0), value, font=vf)
            lbbox = draw.textbbox((0, 0), label, font=label_font)
            col_w = max(vbbox[2] - vbbox[0], lbbox[2] - lbbox[0]) + 8
            draw.text(
                (stat_x - col_w, row_y0 + 8),
                label,
                fill=(255, 255, 255, 130),
                font=label_font,
            )
            col = RED_ACCENT if is_pts else WHITE
            vy = row_y0 + 18 if is_pts else row_y0 + 22
            draw.text((stat_x - col_w, vy), value, fill=col, font=vf)
            stat_x -= col_w + 16

    return row_y1 + ROW_MARGIN_Y


# ── Schedule / next race drawing ───────────────────────────────────────────────


def _draw_schedule_item(
    img: Image.Image,
    draw: ImageDraw.ImageDraw,
    y: int,
    race: JolpicaRace,
) -> int:
    """Draw a single schedule item block. Returns new y position."""
    x0 = ROW_MARGIN_X
    x1 = CARD_W - ROW_MARGIN_X

    # Collect session times
    session_slots: list[tuple[JolpicaSessionSchedule | None, str]] = [
        (race.first_practice, "FP1"),
        (race.sprint_qualifying, "Sprint Quali"),
        (race.second_practice, "FP2"),
        (race.sprint, "Sprint"),
        (race.third_practice, "FP3"),
        (race.qualifying, "Qualifying"),
    ]
    sessions: list[tuple[str, str]] = []
    for slot, label in session_slots:
        t = _session_cst(slot)
        if t:
            sessions.append((label, f"{t} CST"))
    race_time = _utc_to_cst(race.date, race.time)
    sessions.append(("Race", f"{race_time} CST"))

    item_h = 44 + len(sessions) * 14
    row_y1 = y + item_h

    # Background
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ovr_draw = ImageDraw.Draw(overlay)
    ovr_draw.rounded_rectangle(
        (x0, y, x1, row_y1), radius=ROW_RADIUS, fill=(255, 255, 255, 12)
    )
    img.alpha_composite(overlay)

    # Red accent bar
    draw.rectangle((x0, y + 6, x0 + TEAM_BAR_W, row_y1 - 6), fill=RED_ACCENT)

    # Round label + sprint badge
    round_font = _font("Medium", 10)
    round_text = f"ROUND {race.round}"
    draw.text((x0 + 16, y + 8), round_text, fill=(255, 255, 255, 130), font=round_font)

    if race.is_sprint_weekend:
        sprint_font = _font("Bold", 8)
        badge_text = "SPRINT"
        bb = draw.textbbox((0, 0), badge_text, font=sprint_font)
        bw = bb[2] - bb[0] + 12
        bh = bb[3] - bb[1] + 6
        rbbox = draw.textbbox((0, 0), round_text, font=round_font)
        badge_x = x0 + 16 + rbbox[2] - rbbox[0] + 8
        draw.rounded_rectangle(
            (badge_x, y + 8, badge_x + bw, y + 8 + bh), radius=3, fill=RED_ACCENT
        )
        draw.text((badge_x + 6, y + 10), badge_text, fill=WHITE, font=sprint_font)

    # Race name with flag image
    title_font = _font("Bold", 14)
    _draw_flagged_text(
        img,
        draw,
        (x0 + 16, y + 24),
        race.circuit.location.country,
        race.race_name,
        title_font,
        WHITE,
        flag_size=18,
    )

    # Sessions list
    session_font = _font("Regular", 10)
    race_font = _font("SemiBold", 10)
    sy = y + 44
    for label, time_str in sessions:
        is_race = label == "Race"
        f = race_font if is_race else session_font
        col = RED_ACCENT if is_race else (255, 255, 255, 180)
        draw.text((x0 + 28, sy), f"{label}: {time_str}", fill=col, font=f)
        sy += 14

    return row_y1 + ROW_MARGIN_Y + 4


# ── Public render functions ────────────────────────────────────────────────────


def render_race_result(race: JolpicaRace) -> str:
    """Render race result as a PNG image. Returns file path."""
    n = len(race.race_results)
    img, draw, y = _create_card(
        n,
        race.race_name,
        f"ROUND {race.round} · RACE RESULT",
        circuit_id=race.circuit.circuit_id,
        country=race.circuit.location.country,
    )
    for res in race.race_results:
        pos = res.pos_int
        colour = _team_colour(res.constructor.name)
        time_val = res.time.time if res.time else res.status
        stats = [("TIME", time_val or "-"), ("LAPS", res.laps), ("PTS", res.points)]
        y = _draw_driver_row(
            img,
            draw,
            y,
            pos,
            f"{res.driver.given_name} {res.driver.family_name}",
            res.constructor.name,
            colour,
            acronym=res.driver.family_name[:3],
            stats=stats,
        )
    _draw_footer(draw, img.height)
    return _save_image(img)


def render_qualifying_result(race: JolpicaRace) -> str:
    """Render qualifying result as a PNG image. Returns file path."""
    n = len(race.qualifying_results)
    img, draw, y = _create_card(
        n,
        race.race_name,
        f"ROUND {race.round} · QUALIFYING",
        circuit_id=race.circuit.circuit_id,
        country=race.circuit.location.country,
    )
    for res in race.qualifying_results:
        pos = res.pos_int
        colour = _team_colour(res.constructor.name)
        stats = [("Q1", res.q1), ("Q2", res.q2), ("Q3", res.q3)]
        y = _draw_driver_row(
            img,
            draw,
            y,
            pos,
            f"{res.driver.given_name} {res.driver.family_name}",
            res.constructor.name,
            colour,
            acronym=res.driver.family_name[:3],
            stats=stats,
        )
    _draw_footer(draw, img.height)
    return _save_image(img)


def render_sprint_result(race: JolpicaRace) -> str:
    """Render sprint result as a PNG image. Returns file path."""
    n = len(race.sprint_results)
    img, draw, y = _create_card(
        n,
        race.race_name,
        f"ROUND {race.round} · SPRINT",
        circuit_id=race.circuit.circuit_id,
        country=race.circuit.location.country,
    )
    for res in race.sprint_results:
        pos = res.pos_int
        colour = _team_colour(res.constructor.name)
        time_val = res.time.time if res.time else res.status
        stats = [("TIME", time_val or "-"), ("PTS", res.points)]
        y = _draw_driver_row(
            img,
            draw,
            y,
            pos,
            f"{res.driver.given_name} {res.driver.family_name}",
            res.constructor.name,
            colour,
            acronym=res.driver.family_name[:3],
            stats=stats,
        )
    _draw_footer(draw, img.height)
    return _save_image(img)


def render_driver_standings(
    standings: list[JolpicaDriverStanding], limit: int = 10
) -> str:
    """Render driver standings as a PNG image. Returns file path."""
    entries = standings[:limit]
    img, draw, y = _create_card(len(entries), "DRIVER STANDINGS", "CHAMPIONSHIP")
    for entry in entries:
        pos = entry.pos_int
        colour = _team_colour(entry.primary_team)
        stats = [("WINS", entry.wins), ("POINTS", entry.points)]
        y = _draw_driver_row(
            img,
            draw,
            y,
            pos,
            entry.driver.full_name,
            entry.primary_team,
            colour,
            acronym=entry.driver.family_name[:3],
            stats=stats,
        )
    _draw_footer(draw, img.height)
    return _save_image(img)


def render_constructor_standings(
    standings: list[JolpicaConstructorStanding],
) -> str:
    """Render constructor standings as a PNG image. Returns file path."""
    img, draw, y = _create_card(len(standings), "CONSTRUCTOR STANDINGS", "CHAMPIONSHIP")
    for entry in standings:
        pos = entry.pos_int
        colour = _team_colour(entry.constructor.name)
        stats = [("WINS", entry.wins), ("POINTS", entry.points)]
        y = _draw_constructor_row(
            img, draw, y, pos, entry.constructor.name, colour, stats=stats
        )
    _draw_footer(draw, img.height)
    return _save_image(img)


def render_schedule(races: list[JolpicaRace], limit: int = 5) -> str:
    """Render upcoming schedule as a PNG image. Returns file path."""
    now = datetime.now(timezone.utc)
    upcoming: list[JolpicaRace] = []
    for race in races:
        try:
            dt_utc = datetime.strptime(
                f"{race.date}T{race.time.rstrip('Z')}", "%Y-%m-%dT%H:%M:%S"
            ).replace(tzinfo=timezone.utc)
            if dt_utc >= now:
                upcoming.append(race)
        except ValueError:
            continue
    upcoming = upcoming[:limit]

    if not upcoming:
        img = Image.new("RGBA", (CARD_W, HEADER_H + 80), BG_TOP)
        draw = ImageDraw.Draw(img)
        draw.rectangle((0, 0, CARD_W, HEADER_H), fill=HEADER_RED)
        tf = _font("ExtraBold", 18)
        draw.text((28, 28), "SCHEDULE", fill=WHITE, font=tf)
        msg_font = _font("Regular", 14)
        draw.text(
            (CARD_W // 2 - 100, HEADER_H + 24),
            "No upcoming races",
            fill=(255, 255, 255, 130),
            font=msg_font,
        )
        return _save_image(img)

    # Pre-calculate total height
    total_items_h = 0
    for race in upcoming:
        session_slots: list[tuple[JolpicaSessionSchedule | None, str]] = [
            (race.first_practice, "FP1"),
            (race.sprint_qualifying, "Sprint Quali"),
            (race.second_practice, "FP2"),
            (race.sprint, "Sprint"),
            (race.third_practice, "FP3"),
            (race.qualifying, "Qualifying"),
        ]
        n_sessions = sum(1 for s, _ in session_slots if s is not None) + 1
        total_items_h += 44 + n_sessions * 14 + ROW_MARGIN_Y + 4

    total_h = HEADER_H + 8 + total_items_h + FOOTER_H
    season = upcoming[0].season if upcoming else ""

    img = Image.new("RGBA", (CARD_W, total_h), BG_TOP)
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, HEADER_H, CARD_W, total_h), fill=BG_BOT)
    draw.rectangle((0, 0, CARD_W, HEADER_H), fill=HEADER_RED)

    title_font = _font("ExtraBold", 18)
    sub_font = _font("Medium", 11)
    draw.text((28, 18), f"F1 {season}", fill=WHITE, font=title_font)
    draw.text((28, 46), "UPCOMING SCHEDULE", fill=(255, 255, 255, 220), font=sub_font)

    logo = _load_f1_logo(128)
    if logo is not None:
        img.paste(logo, (CARD_W - 28 - logo.width, (HEADER_H - logo.height) // 2), logo)

    y = HEADER_H + 8
    for race in upcoming:
        y = _draw_schedule_item(img, draw, y, race)

    _draw_footer(draw, img.height)
    return _save_image(img)


def render_next_race(race: JolpicaRace) -> str:
    """Render next race weekend timetable as a PNG image. Returns file path."""
    circuit_name = race.circuit.circuit_name
    locality = race.circuit.location.locality
    country = race.circuit.location.country

    session_slots: list[tuple[JolpicaSessionSchedule | None, str]] = [
        (race.first_practice, "FP1"),
        (race.sprint_qualifying, "Sprint Qualifying"),
        (race.second_practice, "FP2"),
        (race.sprint, "Sprint Race"),
        (race.third_practice, "FP3"),
        (race.qualifying, "Qualifying"),
    ]
    sessions: list[tuple[str, str]] = []
    for slot, label in session_slots:
        t = _session_cst(slot)
        if t:
            sessions.append((label, f"{t} CST"))
    race_time = _utc_to_cst(race.date, race.time)
    sessions.append(("Race", f"{race_time} CST"))

    body_h = 100 + len(sessions) * 20 + FOOTER_H
    total_h = HEADER_H + body_h

    img = Image.new("RGBA", (CARD_W, total_h), BG_TOP)
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, HEADER_H, CARD_W, total_h), fill=BG_BOT)
    draw.rectangle((0, 0, CARD_W, HEADER_H), fill=HEADER_RED)

    title_font = _font("ExtraBold", 18)
    sub_font = _font("Medium", 11)
    _draw_flagged_text(
        img, draw, (28, 18), country, race.race_name, title_font, WHITE, flag_size=22
    )
    draw.text(
        (28, 46),
        f"ROUND {race.round} · RACE WEEKEND",
        fill=(255, 255, 255, 220),
        font=sub_font,
    )

    # F1 logo + circuit overlay
    logo = _load_f1_logo(128)
    if logo is not None:
        img.paste(logo, (CARD_W - 28 - logo.width, (HEADER_H - logo.height) // 2), logo)
    circuit_img = _load_circuit_image(race.circuit.circuit_id, 56)
    if circuit_img is not None:
        alpha = circuit_img.split()[3].point(lambda p: int(p * 0.3))
        circuit_img.putalpha(alpha)
        cx = CARD_W - 180 - circuit_img.width
        cy = (HEADER_H - circuit_img.height) // 2
        img.paste(circuit_img, (cx, cy), circuit_img)

    y = HEADER_H + 16

    # Round + sprint badge
    round_font = _font("Medium", 10)
    round_text = f"ROUND {race.round}"
    draw.text((24, y), round_text, fill=(255, 255, 255, 130), font=round_font)
    if race.is_sprint_weekend:
        sprint_font = _font("Bold", 8)
        bb = draw.textbbox((0, 0), "SPRINT WEEKEND", font=sprint_font)
        bw = bb[2] - bb[0] + 12
        bh = bb[3] - bb[1] + 6
        rbbox = draw.textbbox((0, 0), round_text, font=round_font)
        badge_x = 24 + rbbox[2] - rbbox[0] + 8
        draw.rounded_rectangle(
            (badge_x, y, badge_x + bw, y + bh), radius=3, fill=RED_ACCENT
        )
        draw.text((badge_x + 6, y + 2), "SPRINT WEEKEND", fill=WHITE, font=sprint_font)
    y += 18

    # Race name big
    big_font = _font("ExtraBold", 20)
    _draw_flagged_text(
        img, draw, (24, y), country, race.race_name, big_font, WHITE, flag_size=24
    )
    y += 28

    # Circuit info
    info_font = _font("Regular", 11)
    draw.text(
        (24, y),
        f"{circuit_name} · {locality}, {country}",
        fill=(255, 255, 255, 160),
        font=info_font,
    )
    y += 24

    # Section title
    section_font = _font("SemiBold", 11)
    draw.text((24, y), "SCHEDULE (CST)", fill=RED_ACCENT, font=section_font)
    y += 20

    # Sessions
    session_font = _font("Regular", 11)
    race_session_font = _font("SemiBold", 11)
    for label, time_str in sessions:
        is_race = label == "Race"
        f = race_session_font if is_race else session_font
        col = RED_ACCENT if is_race else (255, 255, 255, 180)
        draw.text((36, y), f"{label}: {time_str}", fill=col, font=f)
        y += 20

    _draw_footer(draw, img.height)
    return _save_image(img)


def render_practice_result(
    session: OpenF1Session,
    results: list[OpenF1SessionResult],
    drivers_by_number: dict[int, OpenF1Driver],
    fp_number: str = "1",
    circuit_id: str = "",
) -> str:
    """Render practice result as a PNG image. Returns file path."""
    circuit = session.circuit_short_name or session.location
    n = len(results)
    img, draw, y = _create_card(
        n,
        circuit,
        f"FREE PRACTICE {fp_number} · RESULTS",
        circuit_id=circuit_id,
        country=session.country_name,
    )
    for entry in results:
        pos = entry.position
        drv = drivers_by_number.get(
            entry.driver_number, OpenF1Driver(driver_number=entry.driver_number)
        )
        colour = _parse_openf1_team_colour(drv) or _team_colour(drv.team_name or "")
        lap_time = _format_lap_duration(entry.duration) if entry.duration else "-"
        gap_str = ""
        if isinstance(entry.gap_to_leader, (int, float)) and entry.gap_to_leader > 0:
            gap_str = f"+{entry.gap_to_leader:.3f}s"

        stats = [("BEST LAP", lap_time), ("GAP", gap_str or "LEADER")]
        y = _draw_driver_row(
            img,
            draw,
            y,
            pos,
            drv.display_name,
            drv.team_name or "",
            colour,
            acronym=drv.name_acronym or (drv.last_name or "?")[:3],
            stats=stats,
            headshot_url=drv.headshot_url,
        )
    _draw_footer(draw, img.height)
    return _save_image(img)


def render_starting_grid(
    drivers_by_number: dict[int, OpenF1Driver],
    grid: list[OpenF1Position],
    circuit_id: str = "",
) -> str:
    """Render starting grid as a PNG image. Returns file path."""
    n = len(grid)
    img, draw, y = _create_card(n, "STARTING GRID", "RACE DAY", circuit_id=circuit_id)
    for entry in grid:
        pos = entry.position
        drv = drivers_by_number.get(
            entry.driver_number, OpenF1Driver(driver_number=entry.driver_number)
        )
        colour = _parse_openf1_team_colour(drv) or _team_colour(drv.team_name or "")
        y = _draw_driver_row(
            img,
            draw,
            y,
            pos,
            drv.display_name,
            drv.team_name or "",
            colour,
            acronym=drv.name_acronym or (drv.last_name or "?")[:3],
            headshot_url=drv.headshot_url,
        )
    _draw_footer(draw, img.height)
    return _save_image(img)


def render_weekend_start(race: JolpicaRace) -> str:
    """Render weekend start notification. Returns file path."""
    return render_next_race(race)
