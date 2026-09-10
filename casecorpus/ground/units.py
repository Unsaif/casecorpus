"""Unit normalisation to UCUM-style strings. Clinical-chemistry units are a small closed world;
we map the common spellings explicitly and fall back to pint for the rest."""
from __future__ import annotations

import re

_MAP = {
    "umol/l": "umol/L", "µmol/l": "umol/L", "μmol/l": "umol/L", "micromol/l": "umol/L", "umol/L": "umol/L",
    "mmol/l": "mmol/L", "nmol/l": "nmol/L", "pmol/l": "pmol/L", "mol/l": "mol/L",
    "mg/dl": "mg/dL", "g/dl": "g/dL", "ug/dl": "ug/dL", "µg/dl": "ug/dL", "μg/dl": "ug/dL", "ng/dl": "ng/dL",
    "mg/l": "mg/L", "g/l": "g/L", "ug/l": "ug/L", "µg/l": "ug/L", "μg/l": "ug/L", "ng/l": "ng/L", "ng/ml": "ng/mL", "ug/ml": "ug/mL", "µg/ml": "ug/mL", "μg/ml": "ug/mL", "mg/ml": "mg/mL",
    "nmol/ml": "nmol/mL", "umol/ml": "umol/mL", "µmol/ml": "umol/mL", "pmol/ml": "pmol/mL",
    "u/l": "U/L", "iu/l": "[IU]/L", "mu/l": "mU/L", "miu/l": "m[IU]/L", "ku/l": "kU/L",
    "mmol/mol creatinine": "mmol/mol{creat}", "mmol/mol creat": "mmol/mol{creat}", "mmol/mol cr": "mmol/mol{creat}", "mmol/mol": "mmol/mol{creat}",
    "umol/mmol creatinine": "umol/mmol{creat}", "µmol/mmol creatinine": "umol/mmol{creat}", "μmol/mmol creatinine": "umol/mmol{creat}", "umol/mmol": "umol/mmol{creat}",
    "mg/g creatinine": "mg/g{creat}", "mg/g creat": "mg/g{creat}", "ug/mg creatinine": "ug/mg{creat}",
    "mmhg": "mm[Hg]", "kpa": "kPa", "%": "%", "percent": "%",
    "nmol/h/mg protein": "nmol/h/mg{protein}", "nmol/min/mg protein": "nmol/min/mg{protein}", "nmol/h/mg": "nmol/h/mg{protein}", "umol/h/mg protein": "umol/h/mg{protein}", "pmol/min/mg": "pmol/min/mg{protein}",
    "nmol/17h/mg protein": "nmol/(17.h)/mg{protein}", "nmol/mg protein/h": "nmol/h/mg{protein}",
    "meq/l": "meq/L", "g/kg/day": "g/kg/d", "mg/kg/day": "mg/kg/d", "mg/kg": "mg/kg", "iu/kg": "[IU]/kg",
    "cells/ul": "/uL", "x10^9/l": "10*9/L", "10^9/l": "10*9/L", "x109/l": "10*9/L", "g/mol": "g/mol", "sec": "s", "s": "s", "min": "min",
}


def normalise_unit(unit: str | None) -> str | None:
    if not unit:
        return None
    u = unit.strip()
    key = re.sub(r"\s+", " ", u.lower().replace("litre", "l").replace("liter", "l"))
    key = key.replace("μ", "µ")
    if key in _MAP:
        return _MAP[key]
    key2 = key.replace(" ", "")
    if key2 in _MAP:
        return _MAP[key2]
    try:
        import pint
        ureg = pint.UnitRegistry()
        q = ureg.parse_expression(u.replace("µ", "u").replace("μ", "u"))
        return f"{q.units:~}".replace(" ", "")
    except Exception:
        return u  # keep as written; validator flags unknown units
