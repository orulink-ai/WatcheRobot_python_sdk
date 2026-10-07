import asyncio
import sys
import pytest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parents[1]))


def test_feedback_only_uses_negotiated_public_expression_sdk():
    from body_feedback import BodyFeedback
    async def run():
        calls = []
        robot = SimpleNamespace(supports=lambda cap: cap == 'expression.runtime.v3',
            expression_runtime=SimpleNamespace(start=lambda preset, **kwargs: calls.append(preset),
                                               update=lambda **kwargs: calls.append(kwargs['preset']),
                                               stop=lambda: calls.append('stop')))
        feedback = BodyFeedback(robot)
        await feedback.show('thinking')
        await feedback.show('speaking')
        await feedback.close()
        assert calls == ['thinking', 'speaking', 'stop']
        assert feedback.status == 'released'
    asyncio.run(run())


def test_old_firmware_does_not_receive_unsupported_expression_commands():
    from body_feedback import BodyFeedback
    async def run():
        feedback = BodyFeedback(SimpleNamespace(supports=lambda cap: False))
        await feedback.show('thinking')
        await feedback.close()
        assert feedback.status == 'unsupported'
    asyncio.run(run())


def test_suppression_holds_black_display_without_restarting_state_animations():
    from body_feedback import SuppressedBodyFeedback
    async def run():
        calls = []
        robot = SimpleNamespace(supports=lambda cap: cap == 'expression.runtime.v3',
            expression_runtime=SimpleNamespace(
                start=lambda preset, **kwargs: calls.append((preset, kwargs)),
                stop=lambda: calls.append('stop')))
        feedback = SuppressedBodyFeedback(robot)
        await feedback.disable()
        await feedback.show('thinking')
        await feedback.show('speaking')
        await feedback.close()  # Ending voice must not restore firmware animations.
        await feedback.disable()
        assert calls == [('standby', dict(color='#000000', auto_blink=False, transition_ms=0))]
        assert feedback.status == 'suppressed'
        await feedback.release()
        assert calls[-1] == 'stop'
    asyncio.run(run())


def test_suppression_never_reports_success_when_command_is_rejected():
    from body_feedback import SuppressedBodyFeedback
    async def run():
        def reject(*args, **kwargs):
            raise RuntimeError('busy')
        feedback = SuppressedBodyFeedback(SimpleNamespace(supports=lambda _: True,
            expression_runtime=SimpleNamespace(start=reject)))
        with pytest.raises(RuntimeError, match='busy'):
            await feedback.disable()
        assert feedback.status != 'suppressed'
    asyncio.run(run())
