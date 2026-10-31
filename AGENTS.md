# Project Agent Guidelines

Project knowledge lives in memex: project page `pages/projects/beasty-dms-plugins.md`.

## DMS Plugin Deployment

- These plugins run live on **bunux** (the user's desktop). Shipping means merged to `main`, then deployed on bunux from its primary checkout `/mnt/storage/workspace/projects/beasty-dms-plugins` with `git pull` and `python3 scripts/deploy_plugins.py`, then checked there. From another machine, run those steps over SSH on bunux; a merge or a deploy anywhere else is not live.
- Deploy only `main` to bunux. A deploy replaces the whole plugin tree, so an unmerged preview disappears with the next deploy from any thread.
- `plugins.json` is the source of truth for locally deployed plugins.
- Never point `~/.config/dms/plugins` at an Orca or other disposable Git worktree.
- Never run `dms restart` directly after plugin changes. Run `python3 scripts/deploy_plugins.py` so validation, atomic deployment, restart, and post-restart verification happen together.
- Run `python3 scripts/plugin_doctor.py` when diagnosing missing plugins. Fix every reported plugin issue before restarting DMS.
- Add every new local plugin to `plugins.json` and commit all of its files before pushing.
