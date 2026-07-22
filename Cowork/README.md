# Cowork

A DankMaterialShell launcher plugin for choosing which AI coding agent opens the `~/cowork` workspace.

## Usage

1. Open the DMS launcher.
2. Select **Cowork** in the Browse section, or type `cow` (typing `cowork` also works).
3. Select **Codex**, **Claude**, or **Pi**.

The selected agent opens in Kitty with `~/cowork` as its working directory. Every terminal uses the `cowork` window class so existing compositor rules continue to apply.

Codex starts with `--yolo`, and Claude starts with `--dangerously-skip-permissions`, matching the `co` and `cl` shell aliases.

## Requirements

- DankMaterialShell 1.4.6 or newer
- Kitty
- Zsh
- Codex, Claude, and Pi CLIs available on the interactive Zsh `PATH`
