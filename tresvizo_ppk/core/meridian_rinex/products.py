"""Datos de cada modelo, para las grabaciones que no traen el registro de metadatos de sesión
(firmware anterior o capturas hechas por USB en la Mac).

Lo que trae el registro `$PTVZ,META` manda siempre: el firmware es la fuente de verdad del offset
del case (`base_plan::kCaseOffsetM`) y del modelo de antena.
"""
from __future__ import annotations

PRODUCTS = {
    "MeridianV": {
        "gnss_receiver": "UM980",
        "antenna_model": "HARXON HA-901A",
        # Del asiento de 5/8" a la base de la antena (ARP), cota del CAD final V3.0 (base_plan.h).
        "case_offset_m": 0.116,
        "antenna_pco_m": 0.0,
    },
    "MeridianVI": {
        "gnss_receiver": "LG290P",
        "antenna_model": "AS-ANT3B-OEMSUR",
        "case_offset_m": 0.152,
        "antenna_pco_m": 0.0,
    },
}


def product(model: str | None) -> dict | None:
    if not model:
        return None
    for name, values in PRODUCTS.items():
        if name.lower() == model.strip().lower():
            return {"model": name, **values}
    return None
