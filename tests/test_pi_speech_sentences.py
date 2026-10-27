import importlib.util
from pathlib import Path


def buffer(**kwargs):
    path = Path(__file__).resolve().parents[1] / "examples/joyinside_pi/sentences.py"
    spec = importlib.util.spec_from_file_location("pi_speech_sentences", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.SentenceBuffer(**kwargs)


def test_sentence_boundaries_and_short_commas_do_not_fragment_greetings():
    b = buffer()
    assert b.feed("你好，") == []
    assert b.feed("我是你的助手。还有") == ["你好，我是你的助手。"]
    assert b.feed("第二句！") == ["还有第二句！"]
    assert b.flush() == []


def test_decimal_and_split_latin_word_are_not_treated_as_sentence_end():
    b = buffer()
    assert b.feed("版本 1.") == []
    assert b.feed("2 使用 example.com 地址。") == ["版本 1.2 使用 example.com 地址。"]


def test_unpunctuated_output_is_bounded_and_keeps_unicode_characters():
    b = buffer(max_chars=60)
    text = "你🙂好" * 200
    chunks = b.feed(text) + b.flush()
    assert "".join(chunks) == text
    assert all(len(c) <= 60 and len(c.encode("utf-8")) < 1000 for c in chunks)


def test_idle_flush_waits_for_enough_text_and_final_flush_keeps_tail():
    b = buffer(min_chars=12)
    assert b.feed("太短") == []
    assert b.flush(idle=True) == []
    assert b.feed("但后续文字已经足够触发等待上限") == []
    assert b.flush(idle=True) == ["太短但后续文字已经足够触发等待上限"]
    assert b.feed("结尾") == []
    assert b.flush() == ["结尾"]
