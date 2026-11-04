"""Bounded choreography and firmware procedural-expression parameters."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

EXPRESSIONS = {
    "sleepy": {"preset":"neutral"},
    "curious": {"preset":"neutral"},
    "warm": {"preset":"smile"},
    "wink": {"preset":"sunglasses"},
    "thinking": {"preset":"processing"},
    "speaking": {"preset":"neutral"},
    "listening": {"preset":"neutral"},
    "photo": {"preset":"photo"},
    "sing": {"preset":"sing"},
    "interference": {"preset":"interference"},
    "error": {"preset":"error"},
    **{emotion: {"preset": emotion} for emotion in ("neutral", "smile", "happy", "anger", "concentration", "sad", "speechless")},
}

class Scene(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(default="新的瞬间", min_length=1, max_length=40)
    expression: Literal["sleepy","curious","warm","wink","thinking","speaking","listening","photo","sing","interference","error","neutral","smile","happy","anger","concentration","sad","speechless"] = "curious"
    pan: int = Field(default=90, ge=70, le=110)
    tilt: int = Field(default=120, ge=100, le=125)
    duration_ms: int = Field(default=1000, ge=600, le=3000)
    hold_seconds: float = Field(default=0.4, ge=0, le=5)
    text: str = Field(default="", max_length=240)
    gesture: Literal["none", "peek", "nod", "settle"] = "none"

DEFAULT_SCENES = [
    Scene(title="梦还没醒", expression="sleepy", tilt=103, hold_seconds=1.1),
    Scene(title="偷偷探头", expression="curious", tilt=116, gesture="peek", text="嗯？外面好像比梦里还热闹。"),
    Scene(title="看看左边", expression="curious", pan=105, text="这是我的新家吗？"),
    Scene(title="看看右边", expression="curious", pan=78, gesture="settle", hold_seconds=0.6),
    Scene(title="发现你了", expression="warm", gesture="nod", text="啊，找到你了！我是 Watcher。以后这张桌子，分我一小块就好。"),
    Scene(title="交换名字", expression="curious", text="我该怎么称呼你呀？可以告诉我，我叫……"),
]
