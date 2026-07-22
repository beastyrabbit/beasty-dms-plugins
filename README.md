# beasty-dms-plugins

Personal [DMS](https://github.com/dangass/dms) plugins for niri.

## Plugins

- **QuickJump** — bar buttons to focus running apps (Discord, WhatsApp, Fastmail, Steam, 1Password)
- **CatppuccinWorkspaces** — Catppuccin Mocha workspace indicator
- **CopyQClipboard** — clipboard history via CopyQ (disabled)
- **Quick AI** — type `?` for quick questions, translation, and input or clipboard summaries

## Quick AI setup

Quick AI uses the installed Codex CLI and its existing ChatGPT login; it does not need an API key. It requires DMS 1.5 or newer.

```bash
codex login status
dms ipc call plugin-scan scan
dms ipc call plugins enable quickAi
```

The `?` launcher menu always shows four actions: Quick Ask, Quick Translate, Summarize Input, and Summarize Clipboard. Ask remains the default; press ↓ to choose another action. Ask, Translate, and Summarize Input require text after `?`, while Summarize Clipboard works with an empty `?`. The popup is a read-only formatted result, includes a Copy button, and closes with Escape.

All actions use Terra with low reasoning and standard speed rather than Codex Fast mode. Quick Ask allows web search when freshness matters; translation and summaries have no web access. Quick Translate automatically translates English ↔ German. Summaries stay in the source language and preserve important facts, dates, decisions, and action items.

Selecting Summarize Clipboard sends the current plain-text clipboard contents to Terra. Questions and clipboard contents are sent to Codex over stdin so they do not appear in the process argument list.

The DMS built-in Settings Search normally owns `?`; move its trigger to `ds` before using Quick AI.

Offline verification (no model calls):

```bash
python3 -m unittest discover -s tests -v
QT_QPA_PLATFORM=offscreen /usr/lib/qt6/bin/qmltestrunner -input tests/tst_quick_ai_launcher.qml
qmllint -I /usr/share/quickshell/dms QuickAI/*.qml
python3 scripts/validate_plugin.py QuickAI/plugin.json
```

## License

MIT
