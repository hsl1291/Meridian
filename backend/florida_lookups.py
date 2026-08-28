"""
Static lookups for statewide Florida data.

- DOR county codes (CO_NO in the FDOR cadastral) -> county name.
- DOR land-use codes (DOR_UC) -> human description. Used as the land-use signal
  for the 65 counties where we don't have a real zoning service.
"""

# Florida Department of Revenue county codes (as used in the NAL/SDF tax roll
# and the statewide cadastral CO_NO field). Numbered 11-77.
DOR_COUNTY = {
    11: "Alachua", 12: "Baker", 13: "Bay", 14: "Bradford", 15: "Brevard",
    16: "Broward", 17: "Calhoun", 18: "Charlotte", 19: "Citrus", 20: "Clay",
    21: "Collier", 22: "Columbia", 23: "Miami-Dade", 24: "DeSoto", 25: "Dixie",
    26: "Duval", 27: "Escambia", 28: "Flagler", 29: "Franklin", 30: "Gadsden",
    31: "Gilchrist", 32: "Glades", 33: "Gulf", 34: "Hamilton", 35: "Hardee",
    36: "Hendry", 37: "Hernando", 38: "Highlands", 39: "Hillsborough", 40: "Holmes",
    41: "Indian River", 42: "Jackson", 43: "Jefferson", 44: "Lafayette", 45: "Lake",
    46: "Lee", 47: "Leon", 48: "Levy", 49: "Liberty", 50: "Madison",
    51: "Manatee", 52: "Marion", 53: "Martin", 54: "Monroe", 55: "Nassau",
    56: "Okaloosa", 57: "Okeechobee", 58: "Orange", 59: "Osceola", 60: "Palm Beach",
    61: "Pasco", 62: "Pinellas", 63: "Polk", 64: "Putnam", 65: "St. Johns",
    66: "St. Lucie", 67: "Santa Rosa", 68: "Sarasota", 69: "Seminole", 70: "Sumter",
    71: "Suwannee", 72: "Taylor", 73: "Union", 74: "Volusia", 75: "Wakulla",
    76: "Walton", 77: "Washington",
}


def county_name(co_no) -> str:
    try:
        return DOR_COUNTY.get(int(co_no), f"County {co_no}")
    except (TypeError, ValueError):
        return "Unknown county"


# Florida DOR land-use codes. Stored as 2-4 char strings (sometimes zero-padded
# or with sub-codes); we normalize to the leading integer for lookup.
DOR_USE = {
    0: "Vacant residential",
    1: "Single family",
    2: "Mobile home",
    3: "Multi-family — 10+ units",
    4: "Condominium",
    5: "Cooperative",
    6: "Retirement / life-care",
    7: "Boarding home / migrant camp",
    8: "Multi-family — fewer than 10 units",
    9: "Residential common element / common area",
    10: "Vacant commercial",
    11: "Store, one story",
    12: "Mixed use — store + office/residential",
    13: "Department store",
    14: "Supermarket",
    15: "Regional shopping center",
    16: "Community shopping center",
    17: "Office building — one story",
    18: "Office building — multi-story",
    19: "Professional service building",
    20: "Airport / terminal / marina",
    21: "Restaurant / cafeteria",
    22: "Drive-in restaurant",
    23: "Financial institution",
    24: "Insurance company office",
    25: "Repair service shop",
    26: "Service station",
    27: "Auto sales / repair / storage",
    28: "Parking lot / mobile home park",
    29: "Wholesale / manufacturing outlet",
    30: "Florist / greenhouse",
    31: "Drive-in theater / open stadium",
    32: "Enclosed theater / auditorium",
    33: "Nightclub / bar",
    34: "Bowling / skating / pool hall",
    35: "Tourist attraction",
    36: "Camp",
    37: "Race track",
    38: "Golf course / driving range",
    39: "Hotel / motel",
    40: "Vacant industrial",
    41: "Light manufacturing",
    42: "Heavy industrial",
    43: "Lumber yard / sawmill",
    44: "Packing plant",
    45: "Cannery / produce packing",
    46: "Food processing",
    47: "Mineral processing",
    48: "Warehouse / distribution terminal",
    49: "Open storage",
    50: "Improved agricultural",
    51: "Cropland (soil class I)",
    52: "Cropland (soil class II)",
    53: "Cropland (soil class III)",
    54: "Timberland (site index 90+)",
    55: "Timberland (site index 80-89)",
    56: "Timberland (site index 70-79)",
    57: "Timberland (site index 60-69)",
    58: "Timberland (site index 50-59)",
    59: "Timberland not classified by site index",
    60: "Grazing land (soil class I)",
    61: "Grazing land (soil class II)",
    62: "Grazing land (soil class III)",
    63: "Grazing land (soil class IV)",
    64: "Grazing land (soil class V)",
    65: "Grazing land (soil class VI)",
    66: "Orchard / grove / citrus",
    67: "Poultry / bees / fish / rabbits",
    68: "Dairy / feed lot",
    69: "Ornamentals / nursery",
    70: "Vacant institutional",
    71: "Church",
    72: "Private school / college",
    73: "Privately owned hospital",
    74: "Home for the aged",
    75: "Orphanage / non-profit service",
    76: "Mortuary / cemetery",
    77: "Club / lodge / union hall",
    78: "Sanitarium / convalescent / rest home",
    79: "Cultural organization / facility",
    80: "Vacant governmental",
    81: "Military",
    82: "Forest / park / recreational",
    83: "Public county school",
    84: "College (public)",
    85: "Hospital (public)",
    86: "County (other than schools/colleges/hospitals)",
    87: "State (other)",
    88: "Federal (other)",
    89: "Municipal (other)",
    90: "Leasehold interest",
    91: "Utility (gas/electric/telephone/water/sewer)",
    92: "Mining / petroleum / gas land",
    93: "Subsurface rights",
    94: "Right-of-way / street / road / canal",
    95: "Rivers / lakes / submerged land",
    96: "Sewage disposal / borrow pit / waste land",
    97: "Outdoor recreational / park land",
    98: "Centrally assessed",
    99: "Acreage not zoned agricultural",
}


def use_description(dor_uc) -> str:
    if dor_uc is None:
        return "—"
    s = str(dor_uc).strip()
    if not s:
        return "—"
    # Codes may be "01", "001", "0407" (DOR + sub) — take leading numeric portion
    digits = ""
    for ch in s:
        if ch.isdigit():
            digits += ch
        else:
            break
    if not digits:
        return s
    code = int(digits)
    # Handle 3-4 digit composite codes by taking the first 2 digits
    if code > 99:
        code = int(digits[:2])
    return DOR_USE.get(code, f"DOR use {s}")
