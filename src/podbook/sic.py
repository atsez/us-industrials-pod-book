"""SIC code rules for the backtest universe.

SIC codes come from each company's EDGAR submissions record. Titles below are
copied from the SEC's SIC code list (sec.gov/info/edgar/siccodes.htm).

Two caveats that belong in the note:
* EDGAR only exposes a company's current SIC code, not its history.
* SIC has no conglomerate code. Conglomerates are coded by their main
  business, so some land inside these ranges and some do not. The review
  report shows where the well-known names fall.

REVIEW_ONLY codes are not in the universe. They are listed in the review report
so the inclusion decision is made once, on evidence, and then frozen in
HYPOTHESES.md before any backtest is run.
"""

from __future__ import annotations

AEROSPACE_DEFENCE = "aerospace_defence"
MACHINERY = "machinery"
ELECTRICAL = "electrical_equipment"
SUBSECTORS = (AEROSPACE_DEFENCE, MACHINERY, ELECTRICAL)

SIC_TITLES: dict[int, str] = {
    2670: "Converted paper & paperboard products",
    3420: "Cutlery, handtools & general hardware",
    3430: "Heating equip, except elec & warm air; & plumbing fixtures",
    3433: "Heating equipment, except electric & warm air furnaces",
    3440: "Fabricated structural metal products",
    3443: "Fabricated plate work (boiler shops)",
    3460: "Metal forgings & stampings",
    3480: "Ordnance & accessories (no vehicles/guided missiles)",
    3490: "Miscellaneous fabricated metal products",
    3510: "Engines & turbines",
    3523: "Farm machinery & equipment",
    3524: "Lawn & garden tractors & home lawn & garden equip",
    3530: "Construction, mining & materials handling machinery",
    3531: "Construction machinery & equip",
    3532: "Mining machinery & equip (no oil & gas field)",
    3533: "Oil & gas field machinery & equipment",
    3537: "Industrial trucks, tractors, trailers & stackers",
    3540: "Metalworking machinery & equipment",
    3541: "Machine tools, metal cutting types",
    3550: "Special industry machinery (no metalworking)",
    3555: "Printing trades machinery & equipment",
    3559: "Special industry machinery, NEC",
    3560: "General industrial machinery & equipment",
    3561: "Pumps & pumping equipment",
    3562: "Ball & roller bearings",
    3564: "Industrial & commercial fans, blowers & air purifying equip",
    3567: "Industrial process furnaces & ovens",
    3569: "General industrial machinery & equipment, NEC",
    3580: "Refrigeration & service industry machinery",
    3585: "Air-cond & warm air heating equip & refrigeration equip",
    3590: "Misc industrial & commercial machinery & equipment",
    3600: "Electronic & other electrical equipment (no computer equip)",
    3612: "Power, distribution & specialty transformers",
    3613: "Switchgear & switchboard apparatus",
    3620: "Electrical industrial apparatus",
    3621: "Motors & generators",
    3630: "Household appliances",
    3640: "Electric lighting & wiring equipment",
    3663: "Radio & TV broadcasting & communications equipment",
    3678: "Electronic connectors",
    3679: "Electronic components, NEC",
    3690: "Miscellaneous electrical machinery, equipment & supplies",
    3711: "Motor vehicles & passenger car bodies",
    3713: "Truck & bus bodies",
    3714: "Motor vehicle parts & accessories",
    3715: "Truck trailers",
    3720: "Aircraft & parts",
    3721: "Aircraft",
    3724: "Aircraft engines & engine parts",
    3728: "Aircraft parts & auxiliary equipment, NEC",
    3730: "Ship & boat building & repairing",
    3743: "Railroad equipment",
    3760: "Guided missiles & space vehicles & parts",
    3790: "Miscellaneous transportation equipment",
    3812: "Search, detection, navigation, guidance, aeronautical sys",
    3822: "Auto controls for regulating residential & comml environments",
    3823: "Industrial instruments for measurement, display & control",
    3825: "Instruments for meas & testing of electricity & elec signals",
    3829: "Measuring & controlling devices, NEC",
}

_AD = {3480, 3720, 3721, 3724, 3728, 3730, 3760, 3812}
_MACH = {
    3420, 3490,
    3510, 3523, 3524, 3530, 3531, 3532, 3537, 3540, 3541, 3550, 3555,
    3560, 3561, 3562, 3564, 3567, 3569, 3580, 3585, 3590,
    3743,
    3822,  # Trane; every other filer in this code is small
}
_ELEC = {3600, 3612, 3613, 3620, 3621, 3640, 3690}

SIC_SUBSECTOR: dict[int, str] = {
    **{c: AEROSPACE_DEFENCE for c in _AD},
    **{c: MACHINERY for c in _MACH},
    **{c: ELECTRICAL for c in _ELEC},
}

# Deliberately excluded, with the reason, and shown in the review report.
REVIEW_ONLY: dict[int, str] = {
    2670: "3M may sit here; the code is otherwise paper and packaging",
    3430: "plumbing and heating, closer to building products",
    3433: "heating equipment, closer to building products",
    3440: "structural metal, closer to building products",
    3443: "boiler shops",
    3460: "forgings and stampings, check for aero suppliers",
    3533: "oilfield equipment, an energy business",
    3559: "mostly semiconductor equipment, a tech business",
    3630: "household appliances, consumer",
    3663: "communications equipment, check for defence electronics",
    3678: "connectors, a tech business",
    3679: "electronic components, a tech business",
    3711: "includes truck makers but also car makers",
    3713: "truck and bus bodies",
    3714: "auto parts",
    3715: "truck trailers",
    3790: "misc transport equipment",
    3823: "industrial instruments",
    3825: "electrical test instruments",
    3829: "measuring devices",
}


def parse_sic(value: object) -> int | None:
    """EDGAR gives SIC as a string, sometimes empty. Return an int or None."""
    if value is None:
        return None
    text = str(value).strip()
    if not text.isdigit():
        return None
    return int(text)


def classify_sic(value: object) -> str | None:
    """Subsector for a SIC code, or None if outside the universe."""
    code = parse_sic(value)
    return None if code is None else SIC_SUBSECTOR.get(code)


def is_candidate_sic(value: object) -> bool:
    """True for codes in the universe or on the review list."""
    code = parse_sic(value)
    return code is not None and (code in SIC_SUBSECTOR or code in REVIEW_ONLY)
