# Factory resource IDs

Resource IDs are installed by firmware; `.wapp` packages do not upload them.
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

The work ID is shown after a successful Creator Mode burn. It starts with a
lowercase letter and contains at most 23 lowercase letters, digits, or
underscores. The Application does not open a serial port and does not read the
SD card directly. To remove an installed work, use
`app.robot.works.delete("morning_show")`.

`app.robot.capabilities` reports supported capability domains, not the full
installed resource catalog. Missing resources are rejected by the device with
`not_found`.
