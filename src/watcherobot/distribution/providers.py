"""Explicit distribution platform configuration; no region detection or fallback."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Provider:
    name: str
    catalog: str
    base_url: str

    def repository_url(self, repo_id: str) -> str:
        return f"{self.base_url}/{repo_id}"

    def source_url(self, repo_id: str, commit: str) -> str:
        return f"{self.repository_url(repo_id)}/tree/{commit}"


PROVIDERS = {
    "huggingface": Provider(
        "huggingface", "Orulink/watcherobot-app-store", "https://huggingface.co/spaces"
    ),
    "gitee": Provider("gitee", "orulink-sz/watcherobot-app-store", "https://gitee.com"),
}


def get_provider(name: str) -> Provider:
    try:
        return PROVIDERS[name]
    except KeyError:
        raise ValueError("Select huggingface or gitee explicitly") from None
