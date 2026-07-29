from __future__ import annotations

import pytest

from watcherobot.runtime.daemon.application.session import (
    ApplicationChannel,
    ApplicationSessionRegistry,
    ApplicationState,
    InvalidRunCredentialError,
    SessionOccupiedError,
)


def test_only_current_application_can_hold_the_single_runtime_session() -> None:
    registry = ApplicationSessionRegistry(current_app="watcher_default")

    run = registry.begin_start()

    assert run.app_id == "watcher_default"
    assert run.state is ApplicationState.STARTING
    with pytest.raises(SessionOccupiedError):
        registry.begin_start()


def test_run_credential_is_scoped_to_one_runtime_session() -> None:
    registry = ApplicationSessionRegistry(current_app="watcher_default")
    first_run = registry.begin_start()

    with pytest.raises(InvalidRunCredentialError):
        registry.attach_channel(
            ApplicationChannel.DESKTOP,
            credential="invalid",
        )

    registry.end_run(ApplicationState.ENDED)
    second_run = registry.begin_start()

    assert second_run.credential != first_run.credential
    with pytest.raises(InvalidRunCredentialError):
        registry.attach_channel(
            ApplicationChannel.DESKTOP,
            credential=first_run.credential,
        )


def test_desktop_and_device_channels_complete_one_session() -> None:
    registry = ApplicationSessionRegistry(current_app="watcher_default")
    run = registry.begin_start()

    registry.attach_channel(
        ApplicationChannel.DESKTOP,
        credential=run.credential,
    )
    assert run.state is ApplicationState.STARTING

    registry.attach_channel(
        ApplicationChannel.DEVICE,
        credential=run.credential,
    )
    assert run.state is ApplicationState.RUNNING
    assert run.connected_channels == set(ApplicationChannel)


def test_duplicate_channel_connection_is_rejected() -> None:
    registry = ApplicationSessionRegistry(current_app="watcher_default")
    run = registry.begin_start()
    registry.attach_channel(
        ApplicationChannel.DESKTOP,
        credential=run.credential,
    )

    with pytest.raises(SessionOccupiedError):
        registry.attach_channel(
            ApplicationChannel.DESKTOP,
            credential=run.credential,
        )


def test_required_channel_disconnect_marks_application_abnormal() -> None:
    registry = ApplicationSessionRegistry(current_app="watcher_default")
    run = registry.begin_start()
    for channel in ApplicationChannel:
        registry.attach_channel(channel, credential=run.credential)

    registry.detach_channel(ApplicationChannel.DEVICE)

    assert run.state is ApplicationState.ERROR
    assert run.connected_channels == {ApplicationChannel.DESKTOP}
