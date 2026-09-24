"""Process-local overrides. Tests install a judge or a searcher here."""

from app.judge import Judge
from app.search import DeepSearchResult

judge: Judge | None = None


class Searcher:
    def search(self, query: str, *, provider: str, key: str) -> DeepSearchResult: ...


searcher: Searcher | None = None
