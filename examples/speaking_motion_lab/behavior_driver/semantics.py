"""Replaceable local Chinese intent/emotion policy; independent of chat providers."""
import re


def classify_clause(text: str) -> tuple[str, int]:
    intent = "neutral"
    for name, pattern in (
        ("deny", r"不可以|不能|不行|不要|拒绝|不同意|不对|不是这样"),
        ("affirm", r"没问题|当然可以|可以的|好的|是的|同意|正确|做得对|没错"),
        ("question", r"[？?]|为什么|怎么|是否|吗[。！!\s]*$"),
        ("greeting", r"你好|您好|大家好|早上好|晚上好|欢迎|再见"),
        ("emphasis", r"[！!]|一定|记住|特别|非常"),
    ):
        if re.search(pattern, text):
            intent = name
            break
    feelings = re.sub(r"不(?:是很|太|再|怎么)?(?:开心|高兴)", "难过", text)
    feelings = re.sub(r"(?:不|没有|别)(?:太|再)?(?:生气|愤怒|难过|伤心|担心)", "", feelings)
    for emotion, pattern in (
        (3, "生气|愤怒|气死|讨厌"), (5, "难过|伤心|失落|遗憾|抱歉"),
        (6, "无语|无奈|算了"), (2, "开心|高兴|太棒|好棒|兴奋|太好了"),
        (4, "认真|仔细|注意|专注|思考"), (1, "谢谢|感谢|欢迎|你好|您好"),
    ):
        if re.search(pattern, feelings):
            return intent, emotion
    return intent, 0


def clauses(text: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in re.findall(r"[^。！？!?；;，,\n]+[。！？!?；;，,\n]*", text)
                 if any(c.isalnum() for c in part))
