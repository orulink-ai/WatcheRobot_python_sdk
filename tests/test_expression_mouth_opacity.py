from types import SimpleNamespace

import pytest

from watcherobot.robot import ExpressionRuntimeDomain


def test_mouth_opacity_wire_values():
    calls = []
    domain = ExpressionRuntimeDomain(SimpleNamespace(
        _require_capability=lambda cap: None,
        _command=lambda kind, payload: calls.append((kind, payload))))
    for value in (0, 0.25, 1):
        domain.update(mouth_opacity=value, transition_ms=1800)
    assert [p['mouth_opacity_milli'] for _, p in calls] == [0, 250, 1000]


@pytest.mark.parametrize('value', [-0.1, 1.1, True, '0', float('nan')])
def test_mouth_opacity_rejects_invalid(value):
    domain = ExpressionRuntimeDomain(SimpleNamespace())
    with pytest.raises(TypeError if isinstance(value, (bool, str)) else ValueError):
        domain.update(mouth_opacity=value)


def test_accessory_opacity_wire_values():
    calls = []
    domain = ExpressionRuntimeDomain(SimpleNamespace(
        _require_capability=lambda cap: None,
        _command=lambda kind, payload: calls.append((kind, payload))))
    for value in (0, 0.25, 1):
        domain.update(accessory_opacity=value, transition_ms=1800)
    assert [p['accessory_opacity_milli'] for _, p in calls] == [0, 250, 1000]


@pytest.mark.parametrize('value', [-0.1, 1.1, True, '0', float('nan')])
def test_accessory_opacity_rejects_invalid(value):
    domain = ExpressionRuntimeDomain(SimpleNamespace())
    with pytest.raises(TypeError if isinstance(value, (bool, str)) else ValueError):
        domain.update(accessory_opacity=value)


@pytest.mark.parametrize("accessory", ["provision_scan", "provision_network", "provision_failed"])
def test_provisioning_pages_preserve_opacity_wire_contract(accessory):
    calls = []
    domain = ExpressionRuntimeDomain(SimpleNamespace(
        _require_capability=lambda cap: None,
        _command=lambda kind, payload: calls.append((kind, payload))))
    domain.update(accessory=accessory, accessory_opacity=0.5, mouth_opacity=0)
    assert calls[0][1]["accessory"] == accessory
    assert calls[0][1]["accessory_opacity_milli"] == 500
    assert calls[0][1]["mouth_opacity_milli"] == 0
