"""The first spoken segment is short without damaging text or natural pauses."""
import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1] / "examples" / "joyinside_pi"


def chunks(text, **kwargs):
    spec = importlib.util.spec_from_file_location("pi_short_chunks", ROOT / "agent.py")
    agent = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(agent)
    return list(agent.speech_chunks(text, **kwargs))


def test_first_segment_uses_a_natural_pause_before_28_characters():
    first = "我会先帮你打开手机上的这个应用，"
    text = first + "接下来我会检查当前页面是否已经成功打开并继续完成你的要求。"
    result = chunks(text)
    assert result[0] == first
    assert "".join(result) == text


def test_first_segment_is_28_characters_without_a_natural_pause():
    result = chunks("机器人" * 80)
    assert len(result[0]) == 28
    assert max(map(len, result[1:])) == 80
    assert "".join(result) == "机器人" * 80


@pytest.mark.parametrize("pause", ["，", ",", "、", ";", "；", "："])
def test_first_segment_can_use_each_natural_pause(pause):
    first = "我们可以先检查一下手机屏幕" + pause
    text = first + "随后根据实际显示的内容继续为你完成接下来的操作和验证。"
    assert chunks(text)[0] == first


def test_short_greeting_does_not_create_an_unnecessary_segment():
    text = "好的，接下来我会根据手机屏幕显示的内容继续检查并完成任务。"
    result = chunks(text)
    assert 20 <= len(result[0]) <= 28
    assert result[0] != "好的，"
    assert "".join(result) == text


def test_sentence_closing_quotes_and_repeated_marks_stay_with_the_sentence():
    text = "他说：“可以！！”接下来继续。"
    assert chunks(text) == ["他说：“可以！！”", "接下来继续。"]


def test_short_url_is_kept_whole_when_first_segment_budget_crosses_it():
    prefix = "查看页面后请打开这个网址："
    url = "https://example.com/a"
    text = prefix + url + " 然后继续检查。"
    result = chunks(text)
    assert any(url in chunk for chunk in result)
    assert "".join(result) == text


@pytest.mark.parametrize("token", ["1,234.56", "application_status", "version_1.2.3"])
def test_short_numbers_and_identifiers_are_not_split_at_the_first_budget(token):
    prefix = "现在已经完成了这一步请检查" + " "
    text = prefix + token + " 随后继续完成接下来的操作与验证。"
    result = chunks(text)
    assert any(token in chunk for chunk in result)
    assert "".join(result) == text


def test_multibyte_and_very_long_tokens_still_obey_hard_limits():
    text = "🙂你好" * 50 + "a" * 200 + "，完成！！”"
    result = chunks(text, limit=43)
    assert "".join(result) == text
    assert all(0 < len(chunk) <= 80 and len(chunk.encode()) <= 43 for chunk in result)
    assert all(any(char.isalnum() or char == "🙂" for char in chunk) for chunk in result)
