# beasty-dms-plugins


Personal [DMS](https://github.com/dangass/dms) plugins for Hyprland and niri.

## Plugins

- **QuickJump** — bar buttons to focus running apps (Discord, WhatsApp, Fastmail, Steam, 1Password)
- **Cowork** — launcher picker for opening `~/cowork` with Codex, Claude, or Pi
- **HyprlandWorkspaces** — rainbow Hyprland workspace switcher with named workspaces and scratchpads
- **CatppuccinWorkspaces** — Catppuccin Mocha workspace indicator for niri (disabled)
- **Quick AI** — type `?` for quick questions, translation, and input or clipboard summaries
- **Twitch Follows** — bar count of live followed Twitch channels; click one to open it in mpv with its chat in Chatterino, or add it to an open group that shares one chat window

## Twitch Follows setup

Twitch Follows reads the account you are logged in with in Chatterino (Flatpak or native), so it needs no Twitch app registration or separate token. If the Chatterino login expires, the dropdown says so; log in again in Chatterino. If a fetch fails, for example because the network is not up yet after boot, the dropdown says whether the internet connection or Twitch is the problem and fetches again after 10 seconds, waiting twice as long after each further failure, up to the refresh interval. The dropdown lists who is live with the game they are playing (hover a row for the stream title); it grows with the list and only scrolls once it would run off the screen. Avatars are kept in `~/.cache/twitch-follows/avatars` and downloaded again in the background once they are a week old. Streams play in their own mpv window through streamlink. Chat shows in Chatterino, or drawn in the bottom-left corner of mpv with Twitch, 7TV, BTTV and FFZ emotes when `uv` is installed (it fetches Pillow for the chat overlay) or the system Python has `python-pillow` (read anonymously over Twitch IRC). The chat button in the uosc control bar, or `c`, switches between the two for the stream's whole group: with chat in mpv, every stream of the group draws its chat and the group's Chatterino window waits hidden on the `special:twitch-chat` workspace, so the streams get the full height. New groups start with Chatterino unless the plugin's *Start with chat in the player* setting is on, and without Chatterino chat starts in mpv and the button only hides or shows it. `End`, or the live button next to it, jumps back to live after pausing or seeking back. You can rewind up to about 30 minutes; mpv keeps that buffer in memory (up to 2.3 GB per stream at the highest bitrates). Two more uosc buttons control quality: the highest-quality button (on by default) stays on the best stream your quality setting allows even while it buffers; switch it off for auto quality, which drops a step after about 3 seconds of buffering within a minute and tries one step up after two smooth minutes (waiting twice as long each time a step up buffers again). Quality changes are seamless: the new quality starts in the background and takes over at the next 2-second segment, in the same window and without losing the rewind buffer. The headphones button plays only the audio (about 200 kbit/s instead of several Mbit/s) and keeps a still of the last picture under the chat. Twitch sends its audio-only stream in chunks about 5 seconds apart, so switching to it pauses the sound once for a few seconds while mpv builds up a larger buffer; switching back is seamless, and the picture returns a few seconds later once mpv has played out the audio it had buffered. The buttons go through `twitch_buttons.lua`, which Twitch Follows loads into its mpv windows, and need these entries in `controls=` of `~/.config/mpv/script-opts/uosc.conf`: `<user-data/twitch-chat>cycle:high_quality:twitch-max-quality@twitch_buttons:no/yes!?Highest quality (off: auto quality)`, `<user-data/twitch-chat>cycle:headphones:twitch-audio-only@twitch_buttons:no/yes!?Audio only` and `<user-data/twitch-chat>cycle:chat:twitch-chat-visible@twitch_buttons:yes=chat!/no=speaker_notes_off?Chat in the player or in Chatterino (c)`. Click a channel to open it in a new viewing group (right-click opens it muted at the starting volume, so unmuting brings the sound back at that level; right-click works on the group buttons too). On Hyprland, a group is its streams plus one Chatterino window with only their channels. Every other channel then shows a numbered button per open group: click 1, 2, … to add that channel to the group, where its chat opens as another tab in the group's chat window. Channels you are watching show their group's number instead. With the [hyprweave](https://github.com/beastyrabbit/hyprweave) plugin loaded, a group's streams share the top 75% side by side (two in a row; three or four as 2×2; five or six as 3×2) with the chat across the bottom; fullscreen in mpv covers the screen with that stream alone. Without hyprweave, a group is a tab group. A stream that you close, or whose broadcast ends (also after a raid), leaves its group and takes its chat tab with it; the chat window closes with the group's last stream, and closing the chat window closes the group's streams. Your saved Chatterino layout is not changed. Shared chat windows need the chatterino-slim fork as `chatterino` on `PATH` (its `--instance` and `--slim` options); with the Flatpak, every stream gets its own chat window and group.

```bash
sudo pacman -S streamlink uv
dms ipc call plugin-scan scan
dms ipc call plugins enable twitchFollows
```

Then add the **Twitch Follows** widget to DankBar. DMS Settings → Plugins → Twitch Follows sets the stream quality, the starting volume (default 30%), the refresh interval (default 2 minutes), and whether chat is shown when a stream opens.

## Quick AI setup

Quick AI calls OpenRouter directly and needs `OPENROUTER_API_KEY` in the DMS environment (for example from `~/.config/environment.d/`). The startup check confirms that OpenRouter accepts the key. The **Better answer** button reruns the request on `gpt-6.1-sol` with medium thinking through the CLIProxyAPI provider configured as `model_provider` in `~/.codex/config.toml`, using that provider's bearer token. It requires DMS 1.5 or newer.

```bash
dms ipc call plugin-scan scan
dms ipc call plugins enable quickAi
```

The `?` launcher menu always shows four actions: Quick Ask, Quick Translate, Summarize Input, and Summarize Clipboard. Ask remains the default; press ↓ to choose another action. Ask, Translate, and Summarize Input require text after `?`, while Summarize Clipboard works with an empty `?`. The popup is a read-only formatted result, includes a Copy button, and closes with Escape.

All actions use the OpenRouter model chosen in DMS Settings → Plugins → Quick AI (default `deepseek/deepseek-v4.1-flash`) and its thinking level (default off). Requests go to the lowest-latency provider. In Quick Ask the model can call OpenRouter's web search when freshness matters; OpenRouter bills each search on top of tokens. Translation and summaries have no web access. Quick Translate automatically translates English ↔ German. Summaries stay in the source language and preserve important facts, dates, decisions, and action items.

After an answer or an error, **Better answer** asks `gpt-6.1-sol` (medium thinking, web search for Quick Ask) instead. It is slower, about 5–10 seconds. For a clipboard summary it reads the clipboard again.

Selecting Summarize Clipboard sends the current plain-text clipboard contents to OpenRouter, or to CLIProxyAPI for Better answer.

The DMS built-in Settings Search normally owns `?`; move its trigger to `ds` before using Quick AI.

## Local deployment

The tracked [`plugins.json`](plugins.json) inventory defines the plugins deployed from this repository. Deployments are copied into a stable directory under `~/.local/share/dms-plugins`; DMS never points directly at a disposable development worktree.

Use the guarded deployment command instead of manually changing plugin symlinks or running `dms restart`:

```bash
python3 scripts/deploy_plugins.py
```

It validates every inventory entry and referenced component, rejects untracked plugin files and disposable worktrees, updates all plugin links atomically, restarts DMS, and verifies that every expected plugin ID is discovered. Before it replaces anything, it lists the plugins whose files differ from the live release. It refuses to replace a live release that `HEAD` does not contain, such as an unmerged preview or a deploy with uncommitted changes; pass `--replace-live` once that work is merged or its owner agrees. Diagnose the repository and live deployment without changing anything using:

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
