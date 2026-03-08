# GEMINI.md - F1 Notifier Plugin for AstrBot

This document provides context and guidelines for developing and interacting with the `astrbot_plugin_f1_notifier` project.

## Project Overview

**F1 Notifier** is a plugin for [AstrBot](https://github.com/AstrBotDevs/AstrBot) that provides automatic F1 race weekend notifications and on-demand query commands (e.g., schedules, results, standings).

- **Primary Language:** Python (>= 3.10)
- **Key Libraries:** `aiohttp`, `Pillow`, `cairosvg`, `pydantic`.
- **Data Sources:**
    - [Jolpica-F1 (Ergast API Mirror)](https://api.jolpi.ca/ergast/f1/): Race results, schedules, and standings.
    - [OpenF1](https://api.openf1.org/v1/): Near real-time practice session data.

## Architecture & Module Breakdown

The project follows a modular structure within the `src/astrbot_plugin_f1_notifier/` directory:

- `main.py`: Entry point for AstrBot. Registers the `F1NotifierPlugin` class, handles command routing (`/f1 ...`), and manages the lifecycle of the scheduler.
- `api.py`: Centralized F1 data fetcher. Implements asynchronous API calls and handles error logic using `Success`/`Failure` result types.
- `scheduler.py`: A background `asyncio` task that polls APIs for session starts/ends and broadcasts notifications to subscribed sessions. Persists state using AstrBot's KV store.
- `image_renderer.py`: Generates broadcast-style F1 graphics (PNGs) using Pillow and CairoSVG. Uses local assets (fonts/SVGs) located in the `assets/` directory.
- `formatter.py`: Logic for converting raw API data into human-friendly text messages (Markdown/Plaintext).
- `models.py`: Pydantic data models for structured API responses and internal data representations.

## Key Files & Assets

- `assets/fonts/`: Orbitron fonts for the racing aesthetic.
- `assets/circuits/`: Circuit layout SVGs.
- `metadata.yaml`: Plugin metadata for AstrBot (name, version, author, dependencies).
- `_conf_schema.json`: Configuration schema for AstrBot's Web UI.

## Building and Running

### Prerequisites
- Python 3.10+
- AstrBot environment.
- Dependencies: `pip install aiohttp cairosvg pillow pydantic`.

### Development & Testing
- **Local Testing:** You can run tests via `pytest`.
    ```bash
    pytest tests/
    ```
- **AstrBot Integration:** To run the plugin, place the entire directory in the AstrBot `data/plugins/` folder and enable it via the AstrBot dashboard.
- **Commands (Prefix: `/f1`):**
    - `schedule`: Recent and upcoming races.
    - `next`: Detailed schedule for the next Grand Prix.
    - `result [round]`: Latest or specific race results.
    - `standings [drivers|teams]`: Championship leaderboards.
    - `subscribe`/`unsubscribe`: Manage auto-notifications for a session.

## Development Conventions

- **Result Pattern:** API calls in `api.py` return `Success(value=...)` or `Failure(error=...)`. Always handle both cases using `match` or `if`.
- **Image Rendering:** The plugin supports both text-only and image-enhanced modes. Use `_render_or_text` in `main.py` to handle the `enable_image_render` config flag gracefully.
- **Async First:** All network I/O and scheduling must be non-blocking. Use `aiohttp` and `asyncio`.
- **Data Persistence:** Subscriptions and notification state are stored in AstrBot's KV storage (`star.put_kv_data`). Do not use local files for session-specific state.
- **Scalability:** Image rendering is done at 2x resolution (`SCALE = 2`) for sharpness.

## TODOs & Future Improvements
- [ ] Implement more robust caching for API responses to reduce rate-limit risks.
- [ ] Add more circuit-specific graphics or driver photos if possible.
- [ ] Enhance error messaging for users when APIs are down or data is delayed.
