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
