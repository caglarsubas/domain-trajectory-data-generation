from __future__ import annotations

from typing import Protocol

from trajectory_contract.models import TrajectoryBundle


class SectorPack(Protocol):
    id: str
    label: str
    sub_domains: tuple[str, ...]
    event_namespace: tuple[str, ...]
    state_dimensions: tuple[str, ...]
    languages: tuple[str, ...]
    default_sub_domains: tuple[str, ...]
    lifecycle: object
    pack: object

    def judge_brief(
        self,
        *,
        sub_domains: list[str],
        language: str,
        corpus_excerpt: str,
        cold_start: bool,
    ) -> str: ...

    def steering(self, text: str): ...

    def classify(self, types: list[str]) -> str: ...

    def hard_checks(self, bundle: TrajectoryBundle) -> list[str]: ...

    def generate(self, **kwargs) -> TrajectoryBundle: ...
