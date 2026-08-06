# Factory resource IDs

Resource IDs are installed by firmware; Application source snapshots do not upload them.
Known examples include:

| Domain | Example IDs |
|---|---|
| Behavior | `happy` |
| Animation | `happy`, `smile` |
| Installed sound | `happy` |
| Light effect | `blink`, `breathing`, `rainbow`, `status_pulse` |

Use them from `ApplicationContext.robot`, for example:

```python
job = await asyncio.to_thread(app.robot.behavior.play, "happy")
await asyncio.to_thread(job.wait, 20.0)
```

## Creator Mode works

Creator Mode can install a complete timeline under
`/watche/works/<work_id>/work.json`. Applications call the installed work by
its stable ID through the Daemon-authorized Device channel:

```python
await asyncio.to_thread(app.robot.works.play, "morning_show")
```

Applications can also address expressions without knowing any SD filesystem
path. Official expressions and work-owned expression clips use separate,
source-scoped calls:

```python
# An expression from /watche/official/current.
await asyncio.to_thread(app.robot.expressions.play_official, "happy")

# One expression clip authored inside an installed work.
await asyncio.to_thread(
    app.robot.works.play_expression,
    "morning_show",
    clip_id="face-first",
)
```

`play_official()` requires the firmware capability
`resource.expression.official`; `play_expression()` requires
`resource.work.expression.play`. The SDK checks those advertised capabilities
before sending a command, so an older firmware fails locally with an upgrade
message instead of silently interpreting new fields as a legacy request.

The work expression API uses the stable Creator `clip_id`, not a filesystem path
or a generated content hash. Current work packages keep `schema_version: 2` and
add `clip_id` to each track. This is an additive field: existing v1 and v2 works
remain readable and whole-work playback remains supported, but a legacy work
without `clip_id` cannot be addressed with `play_expression()` until it is
exported and installed again.

The work ID is shown after a successful Creator Mode burn. It starts with a
lowercase letter and contains at most 23 lowercase letters, digits, or
underscores. The Application does not open a serial port and does not read the
SD card directly. To remove an installed work, use
`app.robot.works.delete("morning_show")`.

`app.robot.capabilities` reports supported capability domains, not the full
installed resource catalog. Missing resources are rejected by the device with
`not_found`.

The corresponding wire payloads are source-scoped:

| Intent | Message data |
|---|---|
| Official expression | `{ "source": "official", "resource_id": "happy" }` |
| Work expression | `{ "source": "work", "work_id": "morning_show", "clip_id": "face-first" }` |
| Whole work | `{ "work_id": "morning_show" }` |

Firmware still accepts the legacy `resource.expression.play` payload containing
only `resource_id`, but new SDK code always sends `source`. Explicit source
requests never fall back to the other source when an ID is missing.

### Portable work packages and SD lifecycle

The Daemon owns the maintenance implementation used by Watcher Desktop. A
portable work uses the `.watcher-work.zip` suffix and contains a versioned
`work.json`, `work_manifest.json`, and any work-local assets. The stable
`work_id` identifies the work across computers and SD cards; editing and
installing it again increments `revision` and replaces only that work.

The maintenance REST surface is deliberately transport-neutral:

- `POST /daemon/maintenance/works/export` builds a portable ZIP from a Creator
  composition.
- `POST /daemon/maintenance/works/import` validates a local ZIP and returns the
  exact editable Creator timeline.
- `POST /daemon/maintenance/works/list` reads works through either `serial` or
  `card_reader` and reports missing animation, action, or sound assets.
- `POST /daemon/maintenance/works/read` reads one selected work plus its
  declared Creator source media so another desktop can continue editing it.
- `POST /daemon/maintenance/works/delete` removes one work without changing
  official resources or other works.
- `POST /daemon/maintenance/work` writes one composition or portable package
  through the selected transport.

Reader writes are atomic at `/watche/works/<work_id>` and preserve
`/watche/official`, `/watche/assets`, and every other work. Serial writes use the
firmware `WRSD/2` work transaction and require the advertised work capabilities.
Neither path opens a second device business channel: application playback still
uses `robot.works.play()` through the Daemon-managed Device channel.

Official assets are referenced by resource ID. Work-local servo actions are
bundled into the portable package and registered in the work-local resource
catalog. Supported local GIF/images are converted to AnimPack v2 and local
audio is converted to mono 24 kHz PCM; the original source files remain inside
the work so importing from a reader or device port restores an editable
timeline. Unsupported or oversized source media fails before installation
instead of producing a work that cannot play on the device.
