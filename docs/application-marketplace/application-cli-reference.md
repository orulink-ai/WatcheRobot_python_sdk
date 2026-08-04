# Application CLI Quick Reference

This page is the single command reference for SDK Application development,
publishing, and marketplace inspection.

## Output rule

For manual use, omit `--jsonl`.

| Mode | Intended caller | Output |
| --- | --- | --- |
| Default | Developer in a terminal | Human-friendly English summaries, tables, and progress |
| `--details` | Developer inspecting the marketplace | Full reviewed manifest, source URL, commit, and dependencies |
| `--jsonl` | Watcher Desktop or automation | Stable JSON Lines on stdout |

`--jsonl` is deliberately verbose because it is a machine protocol. Each line is
one `progress`, `result`, or `error` event. Machine callers must use `type`,
`stage`, `code`, `data`, `details`, and the process exit code. They must not parse
the English `message` field as a business contract.

## Recommended developer flow

```powershell
watcherobot app init .\my_app
watcherobot app check .\my_app
watcherobot app run .\my_app
watcherobot app login
watcherobot app publish .\my_app
watcherobot app submit .\my_app
watcherobot app marketplace
watcherobot app marketplace --details
```

## Supported commands

| Command | Purpose | Starts Daemon | Human output |
| --- | --- | --- | --- |
| `init` | Create a publish-ready project without overwriting an existing path | No | Directory, ID, version, SDK range, and next commands |
| `check` | Validate `app.json`, `app.py`, SDK compatibility, dependencies, and publishable files | No | Manifest summary |
| `run` | Select and run a source directory through the SDK Daemon | Yes or reuses it | Start path and final state |
| `package` | Create a local `.wapp` archive for inspection | No | Created package path |
| `login` | Authorize Hugging Face Device Flow; `--status` checks identity and `--force` replaces a valid login | No | Browser URL, code, expiry, and identity |
| `logout` | Remove only the Watcher Hugging Face credential | No | Sign-out confirmation |
| `publish` | Upload a public immutable Space snapshot without changing the official catalog | No | Space, commit, and fixed source URL |
| `submit` | Validate a published commit and open or reuse its official catalog PR; `--commit` selects an exact published revision | No | Commit, source, PR URL, and review status |
| `marketplace` | Load the reviewed official catalog | No | Compact compatibility table |
| `download` | Download and validate one catalog Space at an exact commit into empty staging | No | Application and staging summary |
| `start` | Start the Application currently selected by the Daemon | Yes or reuses it | ID, state, and PID |
| `stop` | Stop the currently running Application | Yes or reuses it | ID and final state |

Installation, installed inventory, selection, and removal belong to Watcher
Desktop. The SDK help therefore does not list `install`, `list`, `select`, or
`uninstall`; old calls return a migration message instead of starting the Daemon.

## Creating a project

Run the interactive form in a terminal:

```powershell
watcherobot app init .\my_app
```

It prompts for the Application ID, display name, author, and short description.
For scripts or a non-interactive terminal, provide every metadata option:

```powershell
watcherobot app init .\my_app `
  --id com.example.my_app `
  --name "My App" `
  --author "Example Team" `
  --description "An example WatcheRobot Application"
```

The command creates `app.json`, `app.py`, `README.md`, `icon.svg`, and
`.gitignore`. It derives `requires_watcherobot` from the installed SDK and
refuses to modify any existing target path.

## Marketplace output

Use the default compact view for routine inspection:

```powershell
watcherobot app marketplace
```

```text
Application Marketplace
Catalog commit: <40-character-commit>
Applications: 2

STATUS        VERSION      NAME                     APPLICATION ID
------------------------------------------------------------------
Compatible    0.1.0        Example App              com.example.app
Incompatible  2.0.0        Future App               com.example.future
```

Use the detailed view when reviewing source or choosing a snapshot:

```powershell
watcherobot app marketplace --details
```

It includes the exact `Space`, `Source`, `Commit`, SDK requirement, author,
description, and dependencies for every reviewed Application.

Watcher Desktop uses the machine form:

```powershell
watcherobot app marketplace --jsonl
```

## Publishing and catalog review

```powershell
watcherobot app login
watcherobot app check .\my_app
watcherobot app publish .\my_app
watcherobot app submit .\my_app
```

`publish` creates or updates the developer's public Space and returns its exact
commit. It does not read or modify the official catalog, so developers can
publish test snapshots repeatedly without opening catalog PRs.

`submit` requires non-empty `description`, `author`, and `icon` fields. It reads
the published `app.json` and icon at the selected commit, verifies that the
local manifest matches that fixed snapshot, and then creates or reuses the
catalog PR. Use `--commit <40-character-commit>` to review a specific published
revision; omitting it submits the current Space HEAD.

A new catalog PR renders the reviewed Manifest, fixed-revision icon, and
immutable source link; `app-list.json` remains a minimal `space_id + commit`
index. `submit` reports one of these catalog states:

- `Pending review`: the official Dataset PR exists and still needs maintainer review.
- `Already listed`: the same immutable commit is already in the official catalog.

The source URL always points at the exact Space commit. A moving Space `main`
branch is not an installable marketplace version.

## Downloading an immutable snapshot

Create an empty staging directory, then use values from the reviewed marketplace:

```powershell
watcherobot app download `
  --space-id <user>/WatcherRobot-<app_id> `
  --commit <40-character-commit> `
  --target .\staging\app
```

The SDK verifies the commit, source limits, manifest, fixed `app.py`, SDK
compatibility, and Space/Application identity before delivering files. It does not
install the candidate or choose the final Desktop directory.

## Desktop sidecar

Packaged Desktop builds call the restricted `watcher-distribution app ...`
entrypoint. It exposes only `check`, `login`, `logout`, `publish`, `submit`,
`marketplace`, and `download`, and it never imports or starts the Daemon.

Examples:

```powershell
watcher-distribution app check .\my_app --jsonl
watcher-distribution app marketplace --jsonl
```

See [Application Distribution Contract](distribution-contract.md) for stable
events, error codes, exit codes, credentials, and cross-repository boundaries.
