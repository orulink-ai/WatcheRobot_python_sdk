import pytest
from watcherobot.inputs import parse_input_event, RollerButtonEvent

def test_confirm_and_frame_context_survive_typed_input():
    data={'source':'roller','action':'press','timestamp_ms':900,'id':4,
          'stream_id':7,'display_sequence':12,'age_ms':35}
    e=parse_input_event(data)
    assert isinstance(e,RollerButtonEvent)
    assert (e.event_id,e.stream_id,e.display_sequence,e.age_ms)==(4,7,12,35)

@pytest.mark.parametrize('key,value',[('id',True),('stream_id',0),('display_sequence',-1),('age_ms','1')])
def test_invalid_present_context_is_not_silently_dropped(key,value):
    data={'source':'screen_touch','action':'tap','x':1,'y':2,'timestamp_ms':4,
          'id':1,'stream_id':1,'display_sequence':1,'age_ms':0}
    data[key]=value
    assert parse_input_event(data) is None

def test_legacy_input_without_display_context_keeps_working():
    e=parse_input_event({'source':'roller','action':'rotate','delta':-1,'timestamp_ms':1})
    assert e.delta==-1 and e.stream_id is None
