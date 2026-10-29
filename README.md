# beasty-dms-plugins


Personal [DMS](https://github.com/dangass/dms) plugins for Hyprland and niri.

## Plugins

- **QuickJump** — bar buttons to focus running apps (Discord, WhatsApp, Fastmail, Steam, 1Password)
- **Cowork** — launcher picker for opening `~/cowork` with Codex, Claude, or Pi
- **HyprlandWorkspaces** — rainbow Hyprland workspace switcher with named workspaces and scratchpads
- **CatppuccinWorkspaces** — Catppuccin Mocha workspace indicator for niri (disabled)
- **Quick AI** — type `?` for quick questions, translation, and input or clipboard summaries
- **Twitch Follows** — bar count of live followed Twitch channels; the dropdown opens a stream in mpv or its chat in Chatterino

## Twitch Follows setup

Twitch Follows reads the account you are logged in with in Chatterino (Flatpak or native), so it needs no Twitch app registration or separate token. If the Chatterino login expires, the dropdown says so; log in again in Chatterino. Streams play in their own mpv window through streamlink, and the chat button opens the channel as a tab in Chatterino.

```bash
sudo pacman -S streamlink
dms ipc call plugin-scan scan
dms ipc call plugins enable twitchFollows
```

Then add the **Twitch Follows** widget to DankBar. DMS Settings → Plugins → Twitch Follows sets the stream quality, the refresh interval (default 2 minutes), and whether offline channels are listed.

## Quick AI setup

Quick AI uses the installed Codex CLI. It runs Codex without your user config (no MCP servers, hooks, or rules) but reuses the `model_provider` from `~/.codex/config.toml`: when that is a custom provider such as CLIProxyAPI, Quick AI sends requests there, passing the provider's bearer token to Codex through an environment variable rather than the argument list. Without a custom provider it falls back to Codex's own ChatGPT login (`codex login status`). The startup check lists the proxy's `/models` to confirm it serves Terra. It requires DMS 1.5 or newer.

```bash
dms ipc call plugin-scan scan
dms ipc call plugins enable quickAi
```

The `?` launcher menu always shows four actions: Quick Ask, Quick Translate, Summarize Input, and Summarize Clipboard. Ask remains the default; press ↓ to choose another action. Ask, Translate, and Summarize Input require text after `?`, while Summarize Clipboard works with an empty `?`. The popup is a read-only formatted result, includes a Copy button, and closes with Escape.

All actions use the model chosen in DMS Settings → Plugins → Quick AI (default `gpt-5.6-terra`) and its thinking level (default low), at standard speed rather than Codex Fast mode. The dropdown lists the text models the proxy serves. Quick Ask allows web search when freshness matters; translation and summaries have no web access. Quick Translate automatically translates English ↔ German. Summaries stay in the source language and preserve important facts, dates, decisions, and action items.

Selecting Summarize Clipboard sends the current plain-text clipboard contents to Terra. Questions and clipboard contents are sent to Codex over stdin so they do not appear in the process argument list.

The DMS built-in Settings Search normally owns `?`; move its trigger to `ds` before using Quick AI.

## Local deployment

The tracked [`plugins.json`](plugins.json) inventory defines the plugins deployed from this repository. Deployments are copied into a stable directory under `~/.local/share/dms-plugins`; DMS never points directly at a disposable development worktree.

Use the guarded deployment command instead of manually changing plugin symlinks or running `dms restart`:

```bash
python3 scripts/deploy_plugins.py
```

It validates every inventory entry and referenced component, rejects untracked plugin files and disposable worktrees, updates all plugin links atomically, restarts DMS, and verifies that every expected plugin ID is discovered. Diagnose the repository and live deployment without changing anything using:

```bash
python3 scripts/plugin_doctor.py
```

Offline verification (no model calls):

```bash
python3 -m unittest discover -s tests -v
QT_QPA_PLATFORM=offscreen /usr/lib/qt6/bin/qmltestrunner -input tests/tst_quick_ai_launcher.qml
qmllint -I "$(python3 scripts/plugin_doctor.py --shell-dir)" QuickAI/*.qml
python3 scripts/validate_plugin.py QuickAI/plugin.json
python3 scripts/plugin_doctor.py --repo-only
```

## License

MIT
