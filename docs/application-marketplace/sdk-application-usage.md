# WatcheRobot SDK Application Development and Publishing Guide

This guide is for developers who build, run, test, and publish Python Applications
with the current WatcheRobot SDK. It also provides the manual acceptance flow for
the `codex/application-marketplace` branch. The current package version is
`0.1.1a1`.

The shortest complete flow is:

```text
local app.json + app.py
  -> SDK validation
  -> SDK Daemon-managed execution
  -> Hugging Face Device Flow login
  -> publish source to the developer's public Space
  -> submit one immutable commit for official catalog review
  -> maintainer review and merge
  -> SDK installs the reviewed immutable commit into an isolated environment
```

## 1. Ownership boundaries

1. `watcherobot app run` starts or reuses the SDK Daemon. The Daemon injects the
   Desktop and Device channels. Do not run `app.py` directly and do not open a
   separate device WebSocket from an Application.
2. `check`, `login`, `logout`, `publish`, `submit`, `marketplace`, `download`,
   `install`, `list`, and `uninstall`
   are SDK distribution operations. They do not start the Daemon. Desktop uses
   the same implementation through the controlled `watcher-distribution` entry
   point.
3. SDK owns download, installation, inventory, and removal. `install` validates
   the reviewed snapshot, uses a Desktop-supplied locked Runtime, creates an
   isolated `.venv`, and atomically writes the local install record. Daemon
   selection, start, and stop remain management actions.
4. `run-installed --store-root ... --app-id ...` is for SDK development and
   acceptance testing of a custom App Store. It launches the App with its
   installed `.venv` through a separate, ephemeral-port Daemon. It never takes
   over Desktop's managed Daemon or its App Store.

## 2. Prepare a Windows test environment

Run these commands from the SDK repository root. Calling executables inside the
virtual environment avoids PowerShell execution-policy and PATH ambiguity.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
.\.venv\Scripts\python.exe -c "import watcherobot; print(watcherobot.__version__)"
.\.venv\Scripts\watcherobot.exe app --help
```

The expected version is `0.1.1a1`. Reinstall after changing the SDK branch or
`pyproject.toml` so the console entry points match the current checkout.

## 3. Create an Application

Use the SDK initializer in a terminal:

```powershell
.\.venv\Scripts\watcherobot.exe app init .\my_sdk_test
```

It prompts for the unique Application ID, display name, author, and short
description. A non-interactive script must pass all four values:

```powershell
.\.venv\Scripts\watcherobot.exe app init .\my_sdk_test `
  --id com.example.sdk_test `
  --name "SDK Test" `
  --author "Your team" `
  --description "Verify the current SDK Application flow"
```

The target must not exist. Initialization does not start the Daemon or access
Hugging Face. It creates:

```text
my_sdk_test/
|-- app.json
|-- app.py
|-- README.md
|-- icon.svg
`-- .gitignore
```

The generated project is immediately valid for `app check`; its initial version
is `0.1.0`, and `requires_watcherobot` is derived from the installed SDK. You may
still copy an example when you explicitly want that example's behavior, but
change its ID and complete its marketplace metadata before publishing.

Publish-ready `app.json`:

```json
{
  "schema_version": 1,
  "id": "com.example.sdk_test",
  "name": "SDK Test",
  "version": "0.1.0",
  "requires_watcherobot": ">=0.1.0a4,<0.2",
  "dependencies": [],
  "description": "Verify the current SDK Application flow",
  "author": "Your Hugging Face username",
  "icon": "icon.svg"
}
```

Rules:

- Required fields are `schema_version`, `id`, `name`, `version`,
  `requires_watcherobot`, and `dependencies`.
- `description` and `author` remain optional for local check, run, and source
  publication, but both must be non-empty before `app submit` accepts the
  Application. `icon` remains optional throughout. Unknown fields are rejected.
- `id` is 1 to 64 characters and uses only lowercase letters, digits, dots,
  underscores, and hyphens.
- `version` is a three-part semantic version. `requires_watcherobot` must include
  the installed SDK version.
- `dependencies` contains standard Python requirement strings, for example
  `requests>=2.32,<3`. It must not replace `watcherobot` with a URL or local path.
- `icon`, when present, must identify a real regular file inside the Application
  root.

Minimal `app.py`:

```python
import asyncio

from watcherobot.application import ApplicationContext


async def main() -> None:
    async with ApplicationContext.from_environment() as app:
        app.logger.info("app_id=%s", app.app_id)
        app.logger.info("device=%s", app.robot.device_info)
        job = await asyncio.to_thread(
            app.robot.behavior.play,
            "happy",
            repeat=1,
        )
        await asyncio.to_thread(job.wait, 20.0)


asyncio.run(main())
```

The main APIs are `app.robot` for typed robot capabilities, `app.desktop` for the
optional Desktop business channel, and `app.logger` for Daemon-captured logs.

## 4. Validate and run locally

Validate the source without starting the Daemon or accessing Hugging Face:

```powershell
.\.venv\Scripts\watcherobot.exe app check .\my_sdk_test
```

Success returns exit code `0` and a readable manifest summary. Desktop automation
uses the separate machine form:

```powershell
.\.venv\Scripts\watcherobot.exe app check .\my_sdk_test --jsonl
```

Its JSONL result ends with a `result` event whose `ok` field is `true`.

Start or inspect the Daemon, then run the Application:

```powershell
.\.venv\Scripts\watcherobot.exe daemon start
.\.venv\Scripts\watcherobot.exe daemon status
.\.venv\Scripts\watcherobot.exe app run .\my_sdk_test
```

The Daemon selects and starts `app.py`, forwards Application logs, and retains the
device connection after the Application exits. `Ctrl+C` requests an Application
stop and returns exit code `130`. If Watcher Desktop owns the current Daemon, stop
the Application from Desktop instead of stopping that Daemon from the CLI.

## 5. Sign in to Hugging Face

Browser sign-in does not authenticate the SDK distribution tool. The SDK uses the
Watcher Desktop public OAuth Device Flow and stores the resulting token only in
Watcher's operating-system credential entry.

Check status first:

```powershell
.\.venv\Scripts\watcherobot.exe app login --status
```

For a first-time developer, use the human-readable command without `--jsonl`:

```powershell
.\.venv\Scripts\watcherobot.exe app login
```

The terminal prints instructions like these:

```text
Authorize Hugging Face in your browser
Open: https://hf.co/oauth/device
Enter code: ABCD-EFGH
Code expires in: 300 seconds
```

Open the URL, enter the displayed code, approve access, and leave the terminal
running until it prints the authenticated username. The SDK never prints the
access token.

Use `--jsonl` only for Desktop or another machine caller:

```powershell
.\.venv\Scripts\watcherobot.exe app login --status --jsonl
.\.venv\Scripts\watcherobot.exe app login --jsonl
```

Machine callers read `progress.data.verification_uri`,
`progress.data.user_code`, and `progress.data.expires_in`. They must make these
values visible in their own UI. They determine success from the `result` event and
stable fields, not from localized prose.

Use `--force` to replace a still-valid saved login. To remove only Watcher's saved
credential, run:

```powershell
.\.venv\Scripts\watcherobot.exe app logout
```

This does not sign out the browser and does not remove credentials stored by the
Hugging Face CLI or another program.

## 6. Publish source and submit it for review

Source publication and marketplace review are separate operations. `publish`
creates or updates the developer's public Space only. `submit` selects an
already-published immutable commit and creates the official catalog pull request.
Confirm that the ID, source, and dependencies are safe to disclose first.

```powershell
.\.venv\Scripts\watcherobot.exe app check .\my_sdk_test
.\.venv\Scripts\watcherobot.exe app publish .\my_sdk_test
```

Publishing rules:

- The public static Space is named
  `<hf_username>/WatcherRobot-<app_id>`.
- The complete validated source set is uploaded. Git metadata, virtual
  environments, caches, credentials, and `.wappignore` matches are excluded.
- The result contains `space_id`, an immutable 40-character `commit`, and a
  fixed source URL. It contains no Catalog or PR state.
- Re-publishing identical source does not create a meaningless commit.
- `publish` never reads or modifies the official Catalog.

When the snapshot is ready for review, submit it separately:

```powershell
.\.venv\Scripts\watcherobot.exe app submit .\my_sdk_test
```

By default this submits the current Space HEAD. To select the exact commit
returned by `publish`, use:

```powershell
.\.venv\Scripts\watcherobot.exe app submit .\my_sdk_test `
  --commit <40-character-commit>
```

Submission rules:

- `description` and `author` must be non-empty. `icon` is optional. When
  provided, it must exist in both the local project and the selected published
  commit; when omitted or empty, presentation clients use the default
  WatcherRobot Application icon.
- The published `app.json` must match the local project. If it differs, publish
  the current project or check out the source matching the selected commit.
- `submit` reads the fixed source and changes only the official Catalog PR; it
  never uploads or rewrites Application source.
- The result contains `space_id`, `commit`, fixed `source_url`, `pr_status`, and
  an optional `pr_url`.
- An existing open pull request for the same commit is reused.
- An open pull request for another commit of the same App produces
  `catalog_pr_conflict`; resolve it before submitting another version.
- A newly created catalog pull request displays the App name, ID, version,
  author, description, SDK requirement, dependencies, and fixed source link. It
  displays the icon path and fixed-revision icon when supplied, otherwise it
  marks the default icon fallback. The catalog file itself still stores only
  `space_id + commit`.
- The App appears in the official marketplace only after a maintainer merges the
  catalog pull request.

## 7. Read, download, and install an approved snapshot

Reading the official marketplace requires no login:

```powershell
.\.venv\Scripts\watcherobot.exe app marketplace
.\.venv\Scripts\watcherobot.exe app marketplace --details
```

The default is a compact compatibility table. `--details` includes the full source
URL, commit, SDK requirement, dependencies, author, and description. Each App points to a fixed
`space_id + commit`, its structured `app.json`, a fixed `source_url`, and SDK
compatibility. Never treat a Space's moving `main` branch as an installed version.

Desktop uses the machine form:

```powershell
.\.venv\Scripts\watcherobot.exe app marketplace --jsonl
```

Download a selected immutable revision into a caller-created empty staging
directory:

```powershell
$staging = Join-Path ([IO.Path]::GetTempPath()) ("watcher-sdk-download-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $staging | Out-Null
.\.venv\Scripts\watcherobot.exe app download `
  --space-id <user>/WatcherRobot-<app_id> `
  --commit <40-character-commit> `
  --target $staging
```

The result commit must exactly match the requested commit. The delivered directory
contains at least `app.json` and `app.py`. A failed candidate is never promoted
to the SDK App Store.

Desktop adds `--jsonl` to the same command and reads only the structured event
fields.

To install the same reviewed commit, pass the App Store root and the locked
Application Runtime bundled with Desktop. The Runtime is copied and verified on
the first install; every App receives its own environment below the App Store:

```powershell
.\.venv\Scripts\watcherobot.exe app install `
  --space-id <user>/WatcherRobot-<app_id> `
  --commit <40-character-commit> `
  --store-root $env:LOCALAPPDATA\WatcherRobot\applications `
  --runtime-root <Desktop-app-runtime-directory>

.\.venv\Scripts\watcherobot.exe app list `
  --store-root $env:LOCALAPPDATA\WatcherRobot\applications

.\.venv\Scripts\watcherobot.exe app uninstall `
  --store-root $env:LOCALAPPDATA\WatcherRobot\applications `
  --app-id <app_id>
```

`install`, `list`, and `uninstall` never start or contact the Daemon. Desktop
will later call these SDK commands and keep responsibility only for presenting
progress and for Daemon selection/start/stop.

## 8. Exit and error handling

With `--jsonl`, use `type`, `code`, stable data fields, and the process exit code.
Do not parse the human-readable `message`.

| Exit code | Meaning |
| --- | --- |
| `0` | Success; the final event is `result` |
| `2` | Local validation failure |
| `3` | Authentication, authorization, or credential failure |
| `4` | Network, Space, immutable commit, catalog, or PR failure |
| `5` | Unclassified internal SDK failure |
| `130` | User cancellation |

Common stable error codes include `app_manifest_missing`,
`app_entrypoint_missing`, `app_manifest_invalid`, `app_sdk_incompatible`,
`app_dependency_invalid`, `auth_required`, `space_ownership_conflict`,
`catalog_pr_conflict`, and `remote_error`.

## 9. Recommended acceptance order

1. Verify the SDK version and `app --help`.
2. Run `check` and read the manifest summary.
3. Pair through the Daemon and run the Application.
4. Check login status, then complete Device Flow if needed.
5. Confirm public disclosure, then run `publish`.
6. Review the returned immutable commit, then run `submit`.
7. Wait for catalog review and merge.
8. Read `marketplace`, inspect `marketplace --details`, and download its exact commit.
9. Test Desktop refresh, install, start, stop, logs, uninstall, and default-App
   fallback.

Do not include tokens, device codes, or local credentials in test reports. The
machine contract and all stable codes are documented in
[Application Distribution Contract](distribution-contract.md). OAuth scopes and
credential ownership are documented in
[Hugging Face OAuth Implementation Contract](hugging-face-oauth.md).
For a compact list of every command and output mode, see the
[Application CLI Quick Reference](application-cli-reference.md).
