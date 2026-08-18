# Application CLI Quick Reference

This page is the focused reference for SDK Application distribution,
publishing, and marketplace inspection. For every `watcherobot` command,
including Runtime and Bluetooth provisioning, see the
[complete CLI reference](../cli-reference.md).

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
cd my_app
watcherobot app run
watcherobot app check .
watcherobot app login
watcherobot app publish .
watcherobot app submit .
watcherobot app marketplace
watcherobot app marketplace --details
```

## Supported commands

| Command | Purpose | Starts Daemon | Human output |
| --- | --- | --- | --- |
| `init` | Create a runnable Hello World project without overwriting an existing path | No | Directory, ID, version, SDK range, and next commands |
| `check` | Validate `app.json`, `app.py`, SDK compatibility, dependencies, and publishable files | No | Manifest summary |
| `run` | Select and run a source directory through the SDK Daemon | Yes or reuses it | Start path and final state |
| `login` | Authorize Hugging Face Device Flow; `--status` checks identity and `--force` replaces a valid login | No | Browser URL, code, expiry, and identity |
| `logout` | Remove only the Watcher Hugging Face credential | No | Sign-out confirmation |
| `publish` | Upload a public immutable Space snapshot without changing the official catalog | No | Space, commit, and fixed source URL |
| `submit` | Validate a published commit and open or reuse its official catalog PR; `--commit` selects an exact published revision | No | Commit, source, PR URL, and review status |
| `marketplace` | Load the reviewed official catalog | No | Compact compatibility table |
| `download` | Download and validate one catalog Space at an exact commit into empty staging | No | Application and staging summary |
| `install` | Download a reviewed fixed commit, create its isolated environment, and atomically install it into the SDK App Store | No | Application, Runtime, commit, and install root |
| `list` | Read the local SDK App Store inventory | No | Compact installed-Application table |
| `run-installed` | Run one installed App from a custom SDK App Store through an isolated Daemon | Starts or reuses isolated Daemon | App status and test Daemon endpoint |
| `uninstall` | Move one installed Application to recoverable local trash | No | Removed ID and trash root |
| `start` | Start the Application currently selected by the Daemon | Yes or reuses it | ID, state, and PID |
| `stop` | Stop the currently running Application | Yes or reuses it | ID and final state |

SDK owns download, installation, inventory, and removal. `install` receives a
Desktop-supplied locked Application Runtime, copies it after verification when
needed, and creates one isolated `.venv` per Application. `list` and `uninstall`
operate on that same SDK App Store root. Application selection remains a Daemon
management action; the SDK does not expose `select`.

## Creating a project

Create a project with development-friendly metadata defaults:

```powershell
watcherobot app init my_app
```

When the directory is omitted, an interactive terminal prompts only for the
directory. Override metadata when preparing the project for publication:

```powershell
watcherobot app init .\my_app `
  --id com.example.my_app `
  --name "My App" `
  --author "Example Team" `
  --description "An example WatcheRobot Application"
```

The command creates `app.json`, `app.py`, `README.md`, `icon.svg`, and
`.gitignore`. It derives `requires_watcherobot` from the installed SDK and
refuses to modify any existing target path. The generated Hello World plays
`happy`, flashes the light when supported, and then keeps a shuffled demo
behavior showcase running until `Ctrl+C`.

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

`submit` requires non-empty `description` and `author` fields. `icon` is
optional: when present, the command reads and verifies it at the selected
commit; when absent, presentation clients use the default WatcherRobot
Application icon. The local manifest must match that fixed snapshot before the
command creates or reuses the catalog PR. Use `--commit <40-character-commit>`
to review a specific published revision; omitting it submits the current Space
HEAD.

A new catalog PR renders the reviewed Manifest and immutable source link. It
renders the fixed-revision icon when supplied, otherwise it marks the default
icon fallback; `app-list.json` remains a minimal `space_id + commit` index.
`submit` reports one of these catalog states:

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
compatibility, and Space/Application identity before delivering files. `download`
does not choose the final App Store directory; use `install` for the complete
atomic local install.

## Installing, listing, and removing

`install` consumes only a reviewed fixed commit. It needs the local SDK App Store
root and the locked Runtime directory that Desktop packages with the SDK:

```powershell
watcherobot app install `
  --space-id <user>/WatcherRobot-<app_id> `
  --commit <40-character-commit> `
  --store-root <app-store-directory> `
  --runtime-root <locked-app-runtime-directory>

watcherobot app list --store-root <app-store-directory>
watcherobot app uninstall --store-root <app-store-directory> --app-id <app_id>
```

The installed layout is one App directory containing `source/`, `.venv/`, and
`install.json`. The JSONL result of `list` also exposes the controlled
`application_directory` and Python `launcher`; Desktop uses those fields when it
asks Daemon to select an App and does not parse `install.json`. Failed installs
remain outside the active App directory; replaced or removed Apps go to
recoverable local trash. None of these commands starts or contacts the Daemon.

For SDK development or isolated acceptance testing, run an App from the same
custom store with its installed virtual environment. This creates or reuses a
separate Daemon rooted at that store, using ephemeral ports; it does not attach
to or modify Desktop's Daemon:

```powershell
watcherobot app run-installed `
  --store-root <app-store-directory> `
  --app-id <application-id>
```

## Desktop sidecar

Packaged Desktop builds call the restricted `watcher-distribution app ...`
entrypoint. It exposes `check`, `login`, `logout`, `publish`, `submit`,
`marketplace`, `download`, `install`, `list`, and `uninstall`; it never imports
or starts the Daemon.

Examples:

```powershell
watcher-distribution app check .\my_app --jsonl
watcher-distribution app marketplace --jsonl
watcher-distribution app list --store-root <app-store-directory> --jsonl
```

See [Application Distribution Contract](distribution-contract.md) for stable
events, error codes, exit codes, credentials, and cross-repository boundaries.
