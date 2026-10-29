# Project Agent Guidelines

Project knowledge lives in memex: project page `pages/projects/beasty-dms-plugins.md`.

## DMS Plugin Deployment

- `plugins.json` is the source of truth for locally deployed plugins.
- Never point `~/.config/dms/plugins` at an Orca or other disposable Git worktree.
- Never run `dms restart` directly after plugin changes. Run `python3 scripts/deploy_plugins.py` so validation, atomic deployment, restart, and post-restart verification happen together.
- Run `python3 scripts/plugin_doctor.py` when diagnosing missing plugins. Fix every reported plugin issue before restarting DMS.
- Add every new local plugin to `plugins.json` and commit all of its files before pushing.
