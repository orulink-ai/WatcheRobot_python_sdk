import pytest
from watcherobot.idle_studio.model import validate_profile, percentages

def profile():
    return {'schema': 3, 'pool_percentages': {'standby': 80, 'feature': 20}, 'behaviors': [
        {'id': 'a', 'name': 'A', 'expression': 'a', 'motion': None, 'sound': None, 'weight': 100, 'group': 'standby'},
        {'id': 'b', 'name': 'B', 'expression': 'b', 'motion': None, 'sound': None, 'weight': 100, 'group': 'feature'},
    ]}

def test_pool_profile_roundtrip_and_conditional_percentages():
    value = profile()
    assert validate_profile(value) == value
    assert percentages(value) == [100, 100]
    result = validate_profile(value)
    result['pool_percentages']['standby'] = 1
    assert value['pool_percentages']['standby'] == 80

@pytest.mark.parametrize('pools', [{'standby':80,'feature':19}, {'standby':101,'feature':-1},
    {'standby':True,'feature':99}, {'standby':0.5,'feature':99.5}, {'standby':100},
    {'standby':80,'feature':20,'other':0}])
def test_invalid_pool_contract(pools):
    value = profile(); value['pool_percentages'] = pools
    with pytest.raises(ValueError): validate_profile(value)

def test_empty_enabled_pool_rejected_and_disabled_pool_allowed():
    value = profile(); value['behaviors'][1]['weight'] = 0
    with pytest.raises(ValueError): validate_profile(value)
    value['pool_percentages'] = {'standby':100,'feature':0}
    assert validate_profile(value) == value
