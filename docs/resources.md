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

`app.robot.capabilities` reports supported capability domains, not the full
installed resource catalog. Missing resources are rejected by the device with
`not_found`.
