"""Process-local overrides. Tests install a judge, a searcher, or a key checker here."""

from app.judge import Judge
from app.providers import KeyCheck
from app.search import DeepSearchResult

judge: Judge | None = None


class Searcher:
    def search(self, query: str, *, provider: str, key: str) -> DeepSearchResult: ...


searcher: Searcher | None = None


class KeyChecker:
    def check(self, provider: str, key: str) -> KeyCheck: ...


key_checker: KeyChecker | None = None


class Fetcher:
    def fetch(self, url: str, kind: str): ...


fetcher: Fetcher | None = None
