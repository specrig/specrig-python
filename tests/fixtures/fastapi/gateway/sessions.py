import requests


def ping(base: str) -> None:
    requests.get(base + "/health", timeout=1)


def fetch(url: str) -> bytes:
    return requests.get(url, timeout=1).content


def follow(links: list[str]) -> list[bytes]:
    ping(links[0])
    return [fetch(link) for link in links]
