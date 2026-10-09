"""Provider-independent behavior contracts and deterministic trajectory tests."""
import copy

import pytest

from behavior_driver import BehaviorDriver, BehaviorRequest
from behavior_driver.semantics import classify_clause


def request(text="是的。不可以。我很难过。", **overrides):
    payload = {
        "schema": "watche.behavior.request.v1",
        "audio": {"durationMs": 4000, "stepMs": 20, "levels": [250] * 200},
        "transcript": text, "emotionMode": "auto", "emotionTarget": 0, "strength": .85,
    }
    payload.update(overrides)
    return BehaviorRequest.from_dict(payload)


@pytest.mark.parametrize("text,intent,emotion", [
    ("不可以这样做。", "deny", 0), ("没问题！", "affirm", 0),
    ("你好，很高兴见到你。", "greeting", 2), ("为什么？", "question", 0),
    ("我不开心。", "neutral", 5), ("我不生气。", "neutral", 0),
])
def test_semantics(text, intent, emotion):
    assert classify_clause(text) == (intent, emotion)


def test_estimated_alignment_skips_pauses_and_silence_does_not_invent_cues():
    driver = BehaviorDriver()
    audio = {"durationMs": 4000, "stepMs": 20, "levels": [0] * 20 + [250] * 70 + [0] * 30 + [250] * 60 + [0] * 20}
    plan = driver.plan(request(audio=audio))
    assert plan.alignment == "estimated"
    assert plan.cues[0].start_ms == 400
    assert plan.cues[-1].end_ms == 3600
    assert all(a.end_ms <= b.start_ms for a, b in zip(plan.cues, plan.cues[1:]))
    silent = copy.deepcopy(audio)
    silent["levels"] = [0] * 200
    assert driver.plan(request(audio=silent)).cues == ()
    assert driver.plan(request("")).alignment == "no-text"


def test_provided_timestamps_and_model_hints_use_the_same_driver():
    driver = BehaviorDriver()
    plan = driver.plan(request("", segments=[
        {"text": "这次试试看", "startMs": 500, "endMs": 1500, "intent": "affirm", "emotion": 2},
        {"text": "另一种说法", "startMs": 2200, "endMs": 3500, "intent": "deny", "emotion": 4},
    ]))
    assert plan.alignment == "provided"
    assert [(c.start_ms, c.end_ms, c.intent, c.emotion) for c in plan.cues] == [(500, 1500, "affirm", 2), (2200, 3500, "deny", 4)]
    assert driver.sample(plan, 1000).pan_deg == 90
    assert driver.sample(plan, 1000).tilt_deg > 110
    assert driver.sample(plan, 1800).pan_deg == 90


def test_distinct_axes_manual_override_gain_and_final_settlement():
    driver = BehaviorDriver()
    nod = driver.plan(request("是的。"))
    shake = driver.plan(request("不可以。"))
    assert max(f.tilt_deg for f in nod.frames) > 112
    assert all(f.pan_deg == 90 for f in nod.frames)
    assert max(abs(f.pan_deg - 90) for f in shake.frames) > 4
    assert all(f.tilt_deg == 108 for f in shake.frames)
    assert nod.frames[-1].to_dict() == {"timeMs": 5200, "panDeg": 90, "tiltDeg": 108, "mouthLevel": 0, "emotionTarget": 0, "speaking": False}
    manual = driver.plan(request("我很开心。", emotionMode="manual", emotionTarget=5))
    assert driver.sample(manual, 2000).emotion_target == 5
    assert driver.sample(manual, 2000).tilt_deg > 108
    zero = driver.plan(request(strength=0))
    assert all(f.pan_deg == 90 and f.tilt_deg == 108 for f in zero.frames)


def test_frame_sampling_is_deterministic_bounded_and_smooth():
    driver = BehaviorDriver()
    plan = driver.plan(request("是的。不可以。开心！我很难过。你觉得呢？", strength=1.2))
    previous = driver.sample(plan, 0)
    for ms in range(10, 5201, 10):
        frame = driver.sample(plan, ms)
        assert 76 <= frame.pan_deg <= 104 and 100 <= frame.tilt_deg <= 122
        assert 0 <= frame.mouth_level <= 1000
        assert abs(frame.pan_deg - previous.pan_deg) < 1
        previous = frame
    assert plan.to_dict() == driver.plan(request("是的。不可以。开心！我很难过。你觉得呢？", strength=1.2)).to_dict()
    for cue in plan.cues:
        before, after = driver.sample(plan, cue.end_ms - .01), driver.sample(plan, cue.end_ms + .01)
        assert abs(before.pan_deg - after.pan_deg) < .01
        assert abs(before.tilt_deg - after.tilt_deg) < .01


def test_canonical_mouth_gain_preserves_silence_and_caps_input():
    driver = BehaviorDriver()
    for level, expected in ((0, 0), (200, 500), (800, 1000)):
        plan = driver.plan(request("", audio={"durationMs": 4000, "stepMs": 20, "levels": [level] * 200}))
        assert driver.sample(plan, 1000).mouth_level == expected
        assert driver.sample(plan, 4000).mouth_level == 0


@pytest.mark.parametrize("patch", [
    {"schema": "wrong"}, {"strength": float("nan")}, {"emotionTarget": True},
    {"audio": {"durationMs": 4000, "stepMs": 20, "levels": [250]}},
    {"audio": {"durationMs": 20, "stepMs": 20, "levels": [1001]}},
    {"segments": [{"text": "是的", "startMs": 0, "endMs": 5000}]},
    {"segments": [{"text": "是的", "startMs": 0, "endMs": 2000}, {"text": "不行", "startMs": 1000, "endMs": 3000}]},
    {"transcript": "字" * 2001}, {"unexpected": True},
    {"strength": 10 ** 1000},
    {"segments": [{"text": "是的", "startMs": 0, "endMs": 1000, "intent": []}]},
])
def test_invalid_provider_requests_are_rejected(patch):
    with pytest.raises(ValueError):
        request(**patch)
