# F1 Notifier development

Read `README.md` for supported commands, delivery semantics, and migration behavior.

Develop inside AstrBot at `data/plugins/astrbot_plugin_f1_notifier`. Use the
AstrBot root `mise.toml` and `.venv` rather than a separate PDM environment:

```bash
mise run sync
mise run test-f1
mise run lint-f1
```

The AstrBot root and plugin directory are separate Git repositories. Keep their
commits scoped to their own files and use Conventional Commits.

Use English comments and Google-style docstrings. Prefer inline logic over
one-off helpers. Test real production functions rather than copying their
implementation into tests. Keep HTTP sessions, caches, and renderers owned by
the plugin instance. Store subscriptions and delivery acknowledgements in
AstrBot KV; store generated images under the plugin data directory.

Never fabricate a grid from qualifying positions or select OpenF1's global
`latest` session for a specific weekend. Pending data must remain pending.
A delivery succeeds only when the platform send succeeds; checkpoint each
recipient independently. Stop and await polling and rendering on unload.
