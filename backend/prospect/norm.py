"""Normalization helpers shared by the DBPR side and the tax-roll side of the
association-to-condo match.

The two sources describe the same building differently:

    DBPR  address   "8450 S.W. 133 AVENUE ROAD, MIAMI, FL 33183"
    NAL   PHY_ADDR1 "8450 SW 133 AVE RD 4"      (per unit, unit number appended)

so both are reduced to a comparable building-address key. Names are normalized
too, but the NAL's S_LEGAL is abbreviated ("CROUSES RESUB PB 21-40") and does not
carry the condo name, so address is the primary match key and name is unavailable
on the tax-roll side.
"""
import re

_NOISE = [
    "CONDOMINIUM", "CONDOMINIUMS", "CONDO", "CONDOS",
    "ASSOCIATION", "ASSOC", "ASSN", "INC", "CORP", "CORPORATION", "THE",
]
_NOISE_RE = re.compile(r"\b(" + "|".join(sorted(_NOISE, key=len, reverse=True)) + r")\b")

# Roman numerals are kept as digits, not dropped: two buildings in one complex
# ("TOWER I" vs "TOWER II") are different assets.
_ROMAN = {"I": "1", "II": "2", "III": "3", "IV": "4", "V": "5", "VI": "6",
          "VII": "7", "VIII": "8", "IX": "9", "X": "10", "XI": "11", "XII": "12"}


# "OPERA TOWER, A CONDOMINIUM" -> dropping only CONDOMINIUM strands the article
# and yields "OPERA TOWER A", which will not match the tax roll's "OPERA TOWER".
# The whole phrase has to go before noise removal.
_ARTICLE_CONDO_RE = re.compile(r"\bAN?\s+(CONDOMINIUMS?|CONDOS?)\b")


def normalize_name(raw: str | None) -> str:
    """Reduce a condo/association name to a comparison key."""
    if not raw:
        return ""
    s = re.sub(r"[^A-Z0-9 ]+", " ", raw.upper())
    s = _ARTICLE_CONDO_RE.sub(" ", s)
    s = re.sub(r"\bNO\b|\bNUMBER\b|\bBLDG\b|\bBUILDING\b|\bPHASE\b|\bSEC\b|\bSECTION\b", " ", s)
    s = _NOISE_RE.sub(" ", s)
    toks = [_ROMAN.get(w, w) for w in s.split()]
    while toks and toks[-1] in ("A", "AN"):   # residue of a trailing ", A CONDO"
        toks.pop()
    return " ".join(toks).strip()


_ABBREV = {
    "STREET": "ST", "AVENUE": "AVE", "AVENIDA": "AVE", "ROAD": "RD", "DRIVE": "DR",
    "BOULEVARD": "BLVD", "COURT": "CT", "PLACE": "PL", "TERRACE": "TER",
    "PARKWAY": "PKWY", "HIGHWAY": "HWY", "LANE": "LN", "CIRCLE": "CIR",
    "CAUSEWAY": "CSWY", "PLAZA": "PLZ", "TRAIL": "TRL", "WALK": "WALK",
    "NORTH": "N", "SOUTH": "S", "EAST": "E", "WEST": "W",
    "NORTHWEST": "NW", "NORTHEAST": "NE", "SOUTHWEST": "SW", "SOUTHEAST": "SE",
}
_ORDINAL_RE = re.compile(r"\b(\d+)(ST|ND|RD|TH)\b")

# Street-type tokens. Everything after the LAST one of these is a unit
# designator, not part of the building address.
_ST_TYPES = {
    "ST", "AVE", "RD", "DR", "BLVD", "CT", "PL", "TER", "PKWY", "HWY", "LN",
    "CIR", "CSWY", "PLZ", "TRL", "WAY", "WALK", "LOOP", "RUN", "PATH", "PT",
    "SQ", "ROW", "XING", "MNR", "PARK", "GDNS", "ISLE",
}
_UNIT_WORDS = {"APT", "UNIT", "STE", "SUITE", "PH", "BLDG", "TH", "LOT", "NO", "FL"}
# A trailing token that reads as a unit designator: "101", "A", "4B", "PH2".
_UNITISH_RE = re.compile(r"\d+[A-Z]?|[A-Z]\d*|[A-Z]?\d+[A-Z]?")


def _tokens(raw: str) -> list[str]:
    s = re.sub(r"[^A-Z0-9 ]+", " ", raw.upper())
    s = _ORDINAL_RE.sub(r"\1", s)
    return [_ABBREV.get(w, w) for w in s.split()]


def normalize_addr(raw: str | None) -> str:
    """Normalize an address without trying to remove a unit."""
    if not raw:
        return ""
    return " ".join(_tokens(raw)).strip()


def building_addr(raw: str | None) -> str:
    """Reduce a unit-level address to its BUILDING address.

        "7821 NW 1 AVE 4"        -> "7821 NW 1 AVE"
        "1717 N BAYSHORE DR 2704"-> "1717 N BAYSHORE DR"
        "8450 SW 133 AVE RD 4"   -> "8450 SW 133 AVE RD"
        "7830 CAMINO REAL"       -> "7830 CAMINO REAL"   (no street type; kept whole)

    Rule: keep everything through the LAST street-type token. When there is no
    street-type token the address is left alone rather than guessed at -- a
    wrong truncation would split one building into several groups.
    """
    if not raw:
        return ""
    toks = _tokens(raw)
    if not toks:
        return ""
    last = max((i for i, t in enumerate(toks) if t in _ST_TYPES), default=None)
    if last is None:
        # No street type ("7701 CAMINO REAL A 101"). Drop explicit unit words,
        # then peel trailing tokens that look like unit designators -- bare
        # digits, a single letter, or letter+digit combos. At least two tokens
        # are always kept so the house number and street name survive.
        out, skip = [], False
        for t in toks:
            if skip:
                skip = False
                continue
            if t in _UNIT_WORDS:
                skip = True
                continue
            out.append(t)
        while len(out) > 2 and _UNITISH_RE.fullmatch(out[-1]):
            out.pop()
        return " ".join(out).strip()
    # "8450 SW 133 AVE RD" -- RD directly after AVE is part of the street name.
    end = last
    if end + 1 < len(toks) and toks[end + 1] in {"RD", "CT", "DR", "TER", "PL", "LN", "CIR"}:
        end += 1
    return " ".join(toks[: end + 1]).strip()


_ENTITY_RE = re.compile(
    r"\b(LLC|L L C|INC|CORP|LP|LLP|LTD|TRUST|TRS|TR|FOUNDATION|PARTNERS|"
    r"PARTNERSHIP|HOLDINGS?|PROPERTIES|PROPERTY|INVESTMENTS?|INVESTMENT|REALTY|"
    r"GROUP|ASSOCIATES|ASSOC|ASSN|ENTERPRISES|VENTURES?|CAPITAL|DEVELOPMENT|"
    r"BANK|FUND|REIT|MANAGEMENT|MGMT|COMPANY)\b"
)


def is_entity(owner: str | None) -> bool:
    """True if the owner name reads as a company/trust rather than a natural
    person. Corporate ownership share correlates with how assemblable a
    building is -- entities sell; homesteaded residents fight."""
    if not owner:
        return False
    return bool(_ENTITY_RE.search(owner.upper()))


def normalize_owner(owner: str | None) -> str:
    """Collapse an owner name for grouping. Deliberately conservative: it fixes
    spacing and punctuation but does NOT try to equate "SMITH JOHN" with
    "JOHN SMITH", because a false merge would inflate the concentration score --
    the single number this whole tool is ranked on."""
    if not owner:
        return ""
    s = re.sub(r"[^A-Z0-9 ]+", " ", owner.upper())
    s = re.sub(r"\b(ET AL|ETAL|JR|SR|III|II|IV)\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


# A Miami-Dade LAND parcel legal for a condo site opens with the condo's name:
#   "BLUE LAGOON CONDO BLUE LAGOON APARTMENTS PB 153-27 TRACTS A & B AS DESC..."
#   "SUNSET VILLAS CONDO NO 3 31 53 41 11.366 AC M/L SUB OF PB 28-10 PT TR..."
# The name runs through the word CONDO plus an optional "NO n" -- the number is
# part of the name, since CONDO NO 3 is a different association from CONDO NO 4.
_LEGAL_NAME_RE = re.compile(
    r"^(.{2,60}?\bCONDO(?:MINIUM)?\b(?:\s+(?:NO|NUM|NUMBER)\.?\s*\d+)?"
    r"(?:\s+(?:PHASE|BLDG|SEC|SECTION)\.?\s*[0-9IVX]+)?)"
)


def condo_name_from_legal(legal: str | None) -> str:
    """Pull the condo's name off the front of a LAND parcel legal description.
    Returns '' when the legal doesn't name a condo. Callers take a majority vote
    across a site's land parcels, so a stray non-condo legal does no harm."""
    if not legal:
        return ""
    m = _LEGAL_NAME_RE.match(legal.upper().strip())
    return m.group(1).strip() if m else ""


_OR_REF = re.compile(r"\b(?:OFF\s+REC|OR)\s+(\d{4,6})[- ](\d{1,5})\b")


def or_refs(text: str | None) -> list[tuple[str, str]]:
    """Official-Records book/page references embedded in free text. For a condo
    unit this is usually the unit's deed, occasionally the declaration -- treated
    as a candidate only, always re-verified in stage 2 against the Clerk's index."""
    if not text:
        return []
    return [(b, p) for b, p in _OR_REF.findall(text.upper())]
