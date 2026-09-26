from __future__ import annotations

from sectors.airline.pack import AIRLINE
from sectors.banking.pack import BANKING
from sectors.hotel.pack import HOTEL
from sectors.base import SectorPack
from sectors.insurance.pack import INSURANCE
from sectors.telecom.pack import TELECOM

# A pack is registered here only once it passes the gates in `sectors.gates`, the same ones banking passes.
_PACKS: dict[str, SectorPack] = {BANKING.id: BANKING, INSURANCE.id: INSURANCE, TELECOM.id: TELECOM, AIRLINE.id: AIRLINE, HOTEL.id: HOTEL}


def known_sectors() -> list[str]:
    return sorted(_PACKS)


def get_sector(sector_id: str) -> SectorPack:
    try:
        return _PACKS[sector_id]
    except KeyError as exc:
        raise ValueError(f"unknown sector: {sector_id}") from exc
