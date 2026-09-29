"""Process-local overrides. Tests install a judge, a searcher, or a key checker here."""

from app.judge import Judge
from app.providers import KeyCheck
from app.search import DeepSearchResult

judge: Judge | None = None
# Tests install an httpx transport here to stand in for the engine when the judge's readiness is checked.
engine_transport = None


class Searcher:
    def search(self, query: str, *, provider: str, key: str) -> DeepSearchResult: ...


searcher: Searcher | None = None


class KeyChecker:
    def check(self, provider: str, key: str) -> KeyCheck: ...


key_checker: KeyChecker | None = None


class Fetcher:
    def fetch(self, url: str, kind: str): ...


fetcher: Fetcher | None = None

# Tests install a factory here that returns an agent with act() and report(), instead of calling providers.
agent_factory = None
