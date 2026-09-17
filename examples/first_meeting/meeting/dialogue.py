"""Deterministic photo consent; cloud-generated text cannot invoke actions."""
from __future__ import annotations

import re
from dataclasses import dataclass

PHOTO_QUESTION = '那我能看一下你吗？我可以给你拍个照吗？'
PHOTO_OK = '好的，我记住你了。接下来你想跟我聊些什么呢？'
GREETING = '这是什么地方呀？旁边都是我没有见过的人。你好呀人类，有什么我可以帮助你的吗？'


@dataclass(frozen=True)
class Decision:
    text: str = ''
    photo: bool = False
    chat: bool = False


class Dialogue:
    def __init__(self):
        self.stage = 'name'
        self.name = ''

    def next(self, text: str) -> Decision:
        compact = re.sub(r'[\s，。！!、,.～~]', '', text)
        if self.stage == 'consent':
            # Only a whole, explicit affirmative grants one capture. Questions,
            # quotations, hypothetical language and embedded negatives never do.
            if compact in {'可以', '可以呀', '可以啊', '可以的', '好', '好的', '好呀', '行', '行啊', '没问题', '我同意', '同意', '拍吧', '可以拍吧', '好拍吧', '你可以给我拍照', '可以给我拍照'}:
                self.stage = 'capturing'
                return Decision(photo=True)
            if any(word in compact for word in ('不', '别拍', '拒绝', '算了')):
                self.stage = 'chat'
                return Decision('好的，我们不拍照。你平时喜欢做什么呀？')
            return Decision('我还没确认你的意思。如果愿意拍照，请说“可以”；不愿意也没关系。')
        if self.stage == 'name':
            match = re.search(r'(?:我叫|我的名字是|叫我)([\u4e00-\u9fffA-Za-z0-9· ]{1,16})', text)
            if match:
                name = re.split(r'你|很高兴|可以|今年', match.group(1))[0].strip()
                if name:
                    self.name = name
                    self.stage = 'consent'
                    return Decision(f'你好，{name}。{PHOTO_QUESTION}')
            return Decision('很高兴见到你！你叫什么名字呀？可以告诉我“我叫……”')
        return Decision(chat=True)
