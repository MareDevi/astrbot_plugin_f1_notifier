# Changelog

## [2.1.0] - 2026-10-31

- Add administrator-only `/f1 push <group UMO> <query> [argument]` for one-time schedule, result, and standings delivery to a specified group, with image-to-text fallback and explicit delivery status.
- Reuse existing query validation without changing subscriptions or automatic notification acknowledgements; reject non-administrators and private-chat targets.
- Skip OpenF1 network requests during every known session's live window, from 30 minutes before the start through 30 minutes after the end, including both boundaries; retain cached responses and use confirmed timing where available.
- Wait at least 31 minutes before polling practice and sprint-qualifying results. Move starting-grid polling from 30 minutes before the race to the interval between 35 and 30 minutes before the race; unpublished grids may not be delivered before the race.
- Log unavailable OpenF1 timing only once per season and round per plugin instance, including repeated failures after recovery.
- Document query-window behavior and administrator push commands in the README, settings, and command help.

## [2.0.2] - 2026-10-30

- Use the logger provided by `astrbot.api` throughout the plugin to comply with plugin review requirements.

## [2.0.1] - 2026-10-30

- Log HTTP 429 once per provider until a successful response resets the warning; suppress repeated rate-limit exceptions in polling and schedule fallback while retaining retries and other error logs.
- Replace the fixed reminder 24 hours before the first session with a configurable Monday, Tuesday, or Wednesday time in plugin settings, defaulting to Monday at 09:00.
- Use the configured timezone and the first session's local calendar week; preserve catch-up until the first session and per-recipient delivery acknowledgements.
- Load next season's schedule when a reminder week crosses New Year.
- Remove the repository-specific `GEMINI.md` development instructions.

## 2.0.0

- Rebuild the plugin around instance-owned HTTP clients, immutable weekend models, and shared text/image tables.
- Preserve all `/f1` commands and add explicit argument validation and timezone labels.
- Deliver sprint and sprint-qualifying results alongside practice, qualifying, race results, weekend reminders, and starting grids.
- Match OpenF1 sessions by type and scheduled time instead of global `latest`.
- Bound caches, pace provider requests, honor rate-limit cooldowns, and treat unpublished datasets as pending.
- Persist successful delivery per season, round, event, and conversation; retry failed recipients independently.
- Restore subscriptions before starting polling and continue checking recently completed weekends.
- Rebuild cards with asynchronous rendering, bounded disk caches, and text fallback.
- Use the AstrBot root Mise environment for dependencies, tests, lint, and startup.

### Migration

- Require AstrBot 4.29.3 or a newer 4.x release.
- Import legacy `f1_subscribers` once into `f1_v2_state`; retain legacy keys for rollback.
- Do not migrate seasonless legacy notification acknowledgements; an upgrade may repeat a result inside the 48-hour catch-up window.
- Remove the unused `headshot_cache_max` setting. New cards do not download driver portraits.
- Keep all command entry points unchanged. Add `timezone`, defaulting to `Asia/Shanghai`.

## [1.1.3] - 2026-03-12

### 🚀 Features

- Update version to 1.1.2 in CHANGELOG, metadata, and pyproject.toml

### 🐛 Bug Fixes

- Use per-round dict for notification state to prevent infinite loop

### 🧪 Testing

- Add comment documenting magic number relationship to scheduler constant
## [1.1.2] - 2026-03-08

### 🚀 Features

- Update version to 1.1.1 in CHANGELOG, metadata, and pyproject.toml; remove unused SVG flag assets
- Add caching logic to API calls and implement rate limit backoff handling

### 🐛 Bug Fixes

- Update position fields to allow None values in models and adjust related logic for handling positions
## [1.1.1] - 2026-03-07

### 🚀 Features

- Update version to 1.1.0 in CHANGELOG, metadata, and pyproject.toml; enhance F1NotifierPlugin description
- Improve rendered image clarity with 2x scale factor
- Draw circuit SVG on right side of each schedule item
- Add headshot_url to race, qualifying, and sprint result models and rendering functions
- Add date_end and race_date_end fields to models and update related functions for accurate session timing
- Enhance image rendering and caching configuration with new parameters

### ⚙️ Miscellaneous Tasks

- Remove unused SVG flag assets
## [1.1.0] - 2026-03-06

### 🚀 Features

- Add headshot and team color to OpenF1Driver model; enhance F1Scheduler to support image broadcasting

### ⚙️ Miscellaneous Tasks

- Update CHANGELOG for version 1.0.2 and improve error handling in API and scheduler
## [1.0.2] - 2026-03-06

### 🚀 Features

- Add F1 Notifier plugin for automated race notifications and query commands
- 更新 README.md，添加 F1 Notifier 插件功能描述和指令查询
- Implement Pydantic models for F1 API responses and refactor scheduler
- Enhance race schedule formatting to include all session times
- Add F1 notification plugin with models, formatter, and scheduler
- Update CHANGELOG and version to 1.0.2; refactor imports and improve error handling in API and scheduler

### 🐛 Bug Fixes

- Eliminate match/case variable scope leakage and refactor scheduler God Function
- Add explicit Failure branches for drivers fetch errors in practice match cases
- Remove _gather wrapper, add concurrent API calls, add broadcast semaphore
- Use asyncio.TimeoutError in api.py and wrap sleep in CancelledError handler in scheduler.py
- Protect error-path asyncio.sleep with nested CancelledError handler and update test

### ⚙️ Miscellaneous Tasks

- 更新许可证信息为 AGPL-3.0
- Bump version to 1.0.1 in metadata and pyproject files
