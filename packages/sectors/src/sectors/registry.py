from __future__ import annotations

from sectors.banking.pack import BANKING
from sectors.base import SectorPack

_PACKS: dict[str, SectorPack] = {BANKING.id: BANKING}


def known_sectors() -> list[str]:
    return sorted(_PACKS)


def get_sector(sector_id: str) -> SectorPack:
    try:
        return _PACKS[sector_id]
    except KeyError as exc:
        raise ValueError(f"unknown sector: {sector_id}") from exc
