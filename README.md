# astrbot_plugin_f1_notifier

🏎 **F1 Notifier** — AstrBot 的 F1 赛事推送与查询插件

自动推送 F1 比赛周通知（赛程提醒、练习赛结果、排位赛结果、冲刺赛结果、发车顺序、正赛结果），同时支持用户通过指令主动查询赛程、积分榜等信息。

---

## 功能特性

### 自动推送（订阅后生效）

| 触发时机 | 推送内容 |
|---|---|
| 距第一个练习赛开始 ≤ 24 小时 | 比赛周开始提醒 + 完整周末赛程表 |
| 每场练习赛结束后 | FP1 / FP2 / FP3 最快圈速排行 |
| 排位赛结束后 | 排位赛完整结果 |
| 冲刺赛结束后（冲刺周末） | 冲刺赛完整结果 |
| 正赛开始前 30 分钟 | 正赛发车顺序 |
| 正赛结束后 | 正赛完整结果 |

### 指令查询

所有指令以 `/f1` 开头：

| 指令 | 说明 |
|---|---|
| `/f1 help` | 显示帮助信息 |
| `/f1 schedule` | 近期赛程（最近 5 站） |
| `/f1 next` | 下一站完整时间表 |
| `/f1 result [round]` | 正赛结果（默认最近一场，可指定站次） |
| `/f1 qualifying [round]` | 排位赛结果（默认最近一场，可指定站次） |
| `/f1 sprint [round]` | 冲刺赛结果（默认最近一场，可指定站次） |
| `/f1 practice [1\|2\|3]` | 练习赛最快圈速（默认 FP1） |
| `/f1 standings drivers` | 车手积分榜 |
| `/f1 standings teams` | 车队积分榜 |
| `/f1 subscribe` | 订阅当前会话的自动推送 |
| `/f1 unsubscribe` | 取消当前会话的自动推送 |
| `/f1 test [season]` | 测试插件各项查询功能（默认 2025 赛季） |

---

## 安装

在 AstrBot 插件管理页面中搜索 `astrbot_plugin_f1_notifier` 并安装，或手动克隆本仓库到插件目录：

```bash
git clone https://github.com/MareDevi/astrbot_plugin_f1_notifier
```

插件依赖 `aiohttp`，AstrBot 会在安装时自动处理。

---

## 使用方法

1. 安装并启用插件后，在任意支持的会话中发送 `/f1 subscribe` 即可订阅自动推送。
2. 发送 `/f1 help` 查看完整指令列表。
3. 发送 `/f1 unsubscribe` 可随时取消订阅。

订阅信息与推送状态会通过 AstrBot KV 存储持久化，重启后自动恢复。

---

## 数据来源

- **[Jolpica-F1](https://api.jolpi.ca/ergast/f1/)** (Ergast API 镜像)：赛程、比赛结果、积分榜
- **[OpenF1](https://api.openf1.org/v1/)**：练习赛实时圈速数据

---

## 依赖

- [AstrBot](https://github.com/AstrBotDevs/AstrBot) >= 适配当前 API 版本
- `aiohttp`

---

## 许可证

[MIT](LICENSE)
