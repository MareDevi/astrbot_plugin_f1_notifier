# F1 Notifier for AstrBot

F1 赛事查询与会话订阅推送插件。支持文字与 F1 风格图片卡片；查询和通知使用相同数据内容。

## 功能与指令

| 指令 | 功能 |
| --- | --- |
| `/f1 help` | 帮助 |
| `/f1 schedule` | 未来 5 站赛程，包含已公布的周末场次 |
| `/f1 next` | 下一站完整周末时间表 |
| `/f1 result [round]` | 正赛结果，默认最近已开始的一站 |
| `/f1 qualifying [round]` | 排位赛结果，包含 Q1 / Q2 / Q3 |
| `/f1 sprint [round]` | 冲刺赛结果 |
| `/f1 practice [1\|2\|3]` | 最近一次指定练习赛结果，也接受 FP1 / FP2 / FP3 |
| `/f1 standings [drivers\|teams]` | 当赛季车手 / 车队积分榜 |
| `/f1 subscribe` | 订阅当前会话 |
| `/f1 unsubscribe` | 取消当前会话订阅 |
| `/f1 test [season]` | 只读检查赛程、结果、发车顺序与积分榜，默认 2025 |

指定站次时查询当年赛季；省略站次时按对应赛段选择最近已开始的一场。当前结果尚未发布时会明确提示，不会拿上一站冒充。年初没有对应场次时，默认查询可以回到上一赛季。休赛期赛程查询会尝试获取下一赛季。

订阅后自动推送：

- 比赛周指定星期与时间：周末完整赛程，默认周一 09:00，可在插件设置中选择周一、周二或周三及具体时间。
- FP1 / FP2 / FP3、排位赛、冲刺排位赛、冲刺赛及正赛结果。
- 正赛开始前 30 分钟：已发布的发车顺序。

结果优先按 OpenF1 已发布的场次结束时间加配置延迟开始轮询；缺失结束时间时使用预计时长，每轮间隔 60 秒；没有发布就继续等待。每个结果最多追补到预计结束后 48 小时。新订阅不补发订阅前已经开始的场次结果；重启后的已有订阅可在有效窗口内继续接收。

周末赛程以首场场次在配置时区中所在的周一至周日为比赛周；无比赛的周不推送。到达配置时间后，在下一轮检查中推送。错过时间、发送失败或新订阅时，可在首场场次开始前补发；首场开始后不再补发。每站每个会话成功发送一次，重载插件或修改时间不会重发已经成功的同站通知。

## 安装

要求 AstrBot **4.29.3 或更新的 4.x 版本**。插件代码支持 Python 3.10+；实际运行版本须同时满足 AstrBot 本体要求。

通过 AstrBot 插件管理页安装，或在 AstrBot 项目根目录执行：

```bash
git clone https://github.com/MareDevi/astrbot_plugin_f1_notifier data/plugins/astrbot_plugin_f1_notifier
```

第三方 Python 依赖声明在 `requirements.txt`，由 AstrBot 插件管理器安装。图片使用 Pillow；已有赛道 SVG 可以通过 CairoSVG 渲染。缺少 Cairo 原生库或赛道素材时，卡片仍可输出，不会生成虚构赛道。中文字体优先使用系统字体，建议 Linux 安装 Noto Sans CJK。时区数据库由 `tzdata` 提供，兼容 Windows。

修改代码或配置后，在 WebUI 的插件页重载插件。开发方式遵循 [AstrBot 插件开发指南](https://docs.astrbot.app/dev/star/plugin-new.html)。

## 配置

| 配置 | 默认值 | 含义 |
| --- | --- | --- |
| `enable_image_render` | `false` | 使用图片卡片；关闭时完全不运行图片渲染 |
| `timezone` | `Asia/Shanghai` | 显示及周末赛程推送使用的 IANA 时区；所有调度比较使用 UTC |
| `weekend_push_day` | `周一` | 比赛周赛程推送星期，可选周一、周二、周三 |
| `weekend_push_time` | `09:00` | 赛程推送时间，24 小时制 `HH:MM`，范围 `00:00`–`23:59` |
| `result_poll_delay` | `5` | 预计场次结束后额外等待的分钟数，0–120 |
| `image_cache_max_age` | `30` | 图片缓存保留分钟数，1–1440 |
| `image_cache_max_count` | `50` | 图片缓存数量上限，1–200 |

图片渲染失败时查询返回文字；主动图片发送抛出异常时尝试文字。平台不支持主动消息或发送失败时保留待发送状态。订阅不代表消息平台一定允许机器人主动推送，详见 [AstrBot 主动消息说明](https://docs.astrbot.app/dev/star/guides/send-message.html)。

## 数据来源与可靠性

- [Jolpica-F1](https://github.com/jolpica/jolpica-f1/blob/main/docs/README.md)：赛程、正赛、排位赛、冲刺赛与积分榜。
- [OpenF1](https://openf1.org/docs/)：场次起止时间、练习赛、冲刺排位赛与正赛发车顺序。通过场次类型和预定开始时间匹配，不使用全局 `latest`。

OpenF1 免费接口提供 2023 年以来的历史数据，实时数据需要订阅。本插件使用公开接口，不承诺实时结果或固定发布延迟。空结果及未发布数据不记为推送成功；遇到 HTTP 429 会遵守 `Retry-After` 冷却。缓存与请求预算由各插件实例独立持有，缓存最多 128 项。

同一数据源持续限流时，仅在首次收到 HTTP 429 时记录一条包含数据源和冷却秒数的警告，不打印异常堆栈。冷却期间的轮询以及冷却结束后再次收到 429 都不重复记录；该数据源成功响应后重置，下一次限流才重新记录。自动重试和待推送状态保留，其他数据源异常照常记录。

订阅与发送记录保存在 AstrBot 插件 KV 中。去重键包含赛季、站次、事件及具体会话。一个会话发送失败，不会导致其他成功会话被重复发送。每次成功后立即持久化；如果进程恰好在平台接受消息后、保存状态前崩溃，重启仍可能重复一次，无法保证端到端恰好一次投递。

图片缓存位于 `data/plugin_data/astrbot_plugin_f1_notifier/images`，不写入插件安装目录。卸载或重载会等待后台轮询和渲染收尾，再释放 HTTP 连接。

### 从 1.x 升级

全部现有 `/f1` 指令继续可用。首次启动自动读取旧 `f1_subscribers`，写入新 `f1_v2_state`，保留旧键供回退。新状态已存在时不会重复导入。

旧 `f1_state` 没有完整赛季与逐会话投递信息，无法安全转换，因此不沿用旧去重记录。升级后，有效的 48 小时结果窗口内可能重发一次旧结果。`headshot_cache_max` 已移除：新版卡片不依赖网络头像；旧配置中的此字段不再生效。

## 开发

插件仓库放在 AstrBot 根目录的 `data/plugins/astrbot_plugin_f1_notifier` 下，使用 **AstrBot 根目录**的 `mise.toml`。当前开发环境固定 Python `3.13.16`、uv `0.13.1`，不会修改全局工具版本。

在 AstrBot 根目录运行：

```bash
mise install
mise run sync
mise run test-f1
mise run lint-f1
mise run check
mise run run
```

`sync` 先运行 AstrBot 的 `uv sync`，再把插件依赖安装到同一个 `.venv`。`test-f1` 测试真实业务模块与 AstrBot 的命令加载；API 测试使用本地临时 HTTP 服务，不依赖外网。不要在生产实例运行测试。

独立插件仓库也可用 `uv run --extra dev pytest` 执行数据、调度和排版测试；命令集成测试需要 AstrBot 依赖以及可导入的 AstrBot 源码，因此推荐使用上面的统一开发环境。

代码结构：

- `main.py`：AstrBot 命令、生命周期、文字与图片发送。
- `models.py`：明确时区的场次、赛程和通用表格模型。
- `api.py`：HTTP 会话、缓存、限流、数据源适配与精确场次匹配。
- `scheduler.py`：订阅、推送窗口和逐会话持久化确认。
- `formatter.py` / `image_renderer.py`：相同表格的文字与图片输出。

以上模块除入口文件外，均位于 `src/astrbot_plugin_f1_notifier/`。变更应附带真实行为测试，提交使用 Conventional Commits。

## 许可证

[AGPL-3.0](LICENSE)。保留原有字体、赛道素材和 Logo 资产。
