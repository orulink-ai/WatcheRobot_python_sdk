import pytest

from watcherobot.idle_studio.model import validate_profile, percentages, SaveSession


def profile():
    return {"schema": 1, "behaviors": [
        {"id": "a", "name": "眨眼", "expression": "blink", "motion": None, "sound": None, "weight": 1},
        {"id": "b", "name": "看看", "expression": "look", "motion": "look", "sound": None, "weight": 2},
    ]}


def test_optional_tracks_and_normalized_probabilities():
    result = validate_profile(profile())
    assert percentages(result) == [33.33, 66.67]


def test_grouped_profile_preserves_schema_and_normalizes_each_pool():
    value = profile()
    value["schema"] = 2
    value["behaviors"][0]["group"] = "standby"
    value["behaviors"][1]["group"] = "feature"
    assert validate_profile(value) == value
    assert percentages(value) == [100, 100]
    value["behaviors"][1]["weight"] = 0
    assert percentages(value) == [100, 0]
    value["behaviors"][1]["group"] = "unknown"
    with pytest.raises(ValueError):
        validate_profile(value)


@pytest.mark.parametrize("value", [-1, True, 1.2, float("nan"), 1000001])
def test_invalid_weight(value):
    value_profile = profile()
    value_profile["behaviors"][0]["weight"] = value
    with pytest.raises(ValueError):
        validate_profile(value_profile)


def test_rejects_empty_all_disabled_duplicate_and_unsafe_resource():
    for rows in ([], [dict(profile()["behaviors"][0], weight=0)],
                 [profile()["behaviors"][0]] * 2,
                 [dict(profile()["behaviors"][0], expression="../secret")]):
        with pytest.raises(ValueError):
            validate_profile({"schema": 1, "behaviors": rows})


def test_save_is_bound_to_device_and_revision():
    session = SaveSession("device-a", 3)
    session.check("device-a", 3)
    with pytest.raises(ValueError, match="设备"):
        session.check("device-b", 3)
    with pytest.raises(ValueError, match="版本"):
        session.check("device-a", 4)


def test_unknown_schema_and_fields_are_rejected():
    with pytest.raises(ValueError):
        validate_profile(dict(profile(), schema=2))
    with pytest.raises(ValueError):
        validate_profile(dict(profile(), unexpected=True))
