"""Play a welcome behavior, then showcase complete silent expressions."""

import asyncio
import random

from watcherobot.application import ApplicationContext


# Friendly one-shot animations; direct animation playback is silent.
SILENT_EXPRESSIONS = (
    "fondle_love",
    "speaking_blink",
    "speaking_eye",
    "click_eye",
    "query",
)
SILENT_EXPRESSION_TIMEOUT_SECONDS = 20.0


def _shuffled_silent_expressions(
    available_ids: set[str],
    previous_expression: str | None,
) -> list[str]:
    expressions = [
        expression_id
        for expression_id in SILENT_EXPRESSIONS
        if expression_id in available_ids
    ]
    random.shuffle(expressions)
    if (
        previous_expression is not None
        and len(expressions) > 1
        and expressions[0] == previous_expression
    ):
        expressions[0], expressions[1] = expressions[1], expressions[0]
    return expressions


async def _keep_awake_without_showcase(app: ApplicationContext) -> None:
    app.logger.info(
        "No compatible silent expressions were advertised; staying awake."
    )
    await asyncio.to_thread(
        app.robot.behavior.play,
        "awake_idle",
        repeat=1,
    )
    await asyncio.Event().wait()


async def _prefetch_expression(
    app: ApplicationContext,
    expression_id: str,
) -> None:
    try:
        await asyncio.to_thread(app.robot.animation.prefetch, expression_id)
    except Exception as exc:
        app.logger.warning(
            "Expression %s could not be prefetched: %s",
            expression_id,
            exc,
        )


async def _showcase_silent_expressions(app: ApplicationContext) -> None:
    if not app.robot.supports("animation"):
        await _keep_awake_without_showcase(app)
        return

    available_ids = set(app.robot.animation.available_ids)
    previous_expression = None
    while True:
        expressions = _shuffled_silent_expressions(
            available_ids,
            previous_expression,
        )
        if not expressions:
            await _keep_awake_without_showcase(app)
            return
        for index, expression_id in enumerate(expressions):
            if index == 0:
                await _prefetch_expression(app, expression_id)
            job = await asyncio.to_thread(
                app.robot.animation.play,
                expression_id,
            )
            if index + 1 < len(expressions):
                await _prefetch_expression(app, expressions[index + 1])
            app.logger.info("Playing silent expression: %s", expression_id)
            await asyncio.to_thread(
                job.wait,
                SILENT_EXPRESSION_TIMEOUT_SECONDS,
            )
            previous_expression = expression_id


async def main() -> None:
    async with ApplicationContext.from_environment() as app:
        app.logger.info("device=%s", app.robot.device_info)
        await asyncio.to_thread(
            app.robot.behavior.play("happy", repeat=1).wait,
            20.0,
        )
        app.logger.info(
            "Welcome complete; starting the silent expression showcase."
        )
        await _showcase_silent_expressions(app)


if __name__ == "__main__":
    asyncio.run(main())
