"""Who is behind an LLC owner, from Florida's corporate registry (Sunbiz).

An assembler who holds each unit in its own entity defeats the single-name
concentration figure, and the shared-mailing and name-series rules in
beneficial.py only catch the ones who reuse an address or a naming pattern.
The registry names each entity's officers and managers; two owner LLCs that
share a MANAGER are, very often, one buyer.

Every choice here leans the way beneficial.py does -- **a false merge is worse
than a missed one**, because concentration is the number the screen is ranked
on:

  * An owner is resolved only on an EXACT normalized name match. Sunbiz pads
    its results with alphabetical neighbours, and the fuzzy matching the
    map's entity panel uses for display (60% similarity) would attach one
    company's officers to another.
  * Only officers who are PEOPLE link owners. Registered agents never do:
    they are commercial services that represent thousands of unrelated
    companies. An officer that is itself a company is recorded but not
    linked on, for the same reason.
  * An officer shared by more than MAX_OWNERS_PER_OFFICER owners in one
    building is a nominee, an attorney or a management firm, not a buyer, and
    is dropped (the same cap beneficial.py applies to mail drops).

Lookups are slow (two requests per entity against a site behind a WAF), so
they run on demand, a bounded number per call, and are cached in prospect.db.
The clustering itself only ever reads the cache and never touches the network.
"""
from __future__ import annotations

import json
import re
import sqlite3
import urllib.parse
from datetime import datetime, timedelta
from typing import Callable

SUNBIZ = "https://search.sunbiz.org"
SEARCH = SUNBIZ + "/Inquiry/CorporationSearch/SearchResults?inquiryType=EntityName&searchTerm="
MAX_OWNERS_PER_OFFICER = 12
DEFAULT_BATCH = 40
MAX_AGE_DAYS = 180

_ENTITY_WORDS = re.compile(
    r"\b(LLC|INC|CORP|CORPORATION|CO|COMPANY|LTD|LP|LLP|LLLP|PLLC|PA|TRUST|HOLDINGS|"
    r"PARTNERS|GROUP|PROPERTIES|INVESTMENTS|REALTY|VENTURES|CAPITAL|ENTERPRISES|"
    r"MANAGEMENT|ASSOCIATES|FUND|BANK)\b")

DDL = """CREATE TABLE IF NOT EXISTS main.sunbiz_entity (
    name_norm  TEXT PRIMARY KEY,   -- norm_entity(owner name as on the roll)
    found      INTEGER NOT NULL,   -- 0 = no exact match in the registry
    doc_number TEXT,
    status     TEXT,
    officers   TEXT,               -- JSON [{"name", "title"}]
    agent      TEXT,
    fetched_at TEXT NOT NULL
)"""


def norm_entity(s: str | None) -> str:
    s = (s or "").upper().replace("&", " AND ")
    s = re.sub(r"\bL\.?\s*L\.?\s*C\.?", "LLC", s)
    s = re.sub(r"\bI\.?N\.?C\.?\b", "INC", s)
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def norm_person(s: str | None) -> str:
    s = re.sub(r"[^A-Z0-9, ]", " ", (s or "").upper())
    s = re.sub(r"\s*,\s*", ", ", s)
    return re.sub(r"\s+", " ", s).strip(" ,")


def is_entity(name: str) -> bool:
    return bool(_ENTITY_WORDS.search(norm_entity(name)))


def ensure(con: sqlite3.Connection) -> None:
    con.execute(DDL)


def _default_fetch(url: str) -> str | None:
    from ..app import _curl_get      # Sunbiz 403s httpx; the app already shells to curl
    return _curl_get(url)


def lookup(name: str, fetch: Callable[[str], str | None] = _default_fetch) -> dict | None:
    """The registry record for exactly this entity name, or {"found": False}.
    None means the registry could not be reached -- which is NOT cached, so
    a network blip never records an owner as unregistered."""
    html = fetch(SEARCH + urllib.parse.quote(name))
    if html is None:
        return None
    target = norm_entity(name)
    rows = re.findall(r'<a href="(/Inquiry/CorporationSearch/SearchResultDetail[^"]+)"[^>]*>([^<]+)</a>',
                      html)
    import html as _html
    hit = next((href for href, label in rows
                if norm_entity(_html.unescape(label)) == target), None)
    if not hit:
        return {"found": False}
    detail = fetch(SUNBIZ + _html.unescape(hit))
    if detail is None:
        return None
    from ..app import _parse_sunbiz_detail
    d = _parse_sunbiz_detail(detail)
    officers = [{"name": o.get("name"), "title": o.get("title")}
                for o in d.get("officers") or [] if o.get("name")]
    return {"found": True, "doc_number": d.get("document_number"), "status": d.get("status"),
            "officers": officers,
            "agent": (d.get("registered_agent") or {}).get("name")}


def resolve(con: sqlite3.Connection, names: list[str],
            fetch: Callable[[str], str | None] = _default_fetch,
            batch: int = DEFAULT_BATCH, max_age_days: int = MAX_AGE_DAYS) -> dict:
    """Look up the entity names not already cached (or cached too long ago),
    at most `batch` per call. Returns what it did and what is left."""
    ensure(con)
    cutoff = (datetime.now() - timedelta(days=max_age_days)).isoformat(timespec="seconds")
    fresh = {r[0] for r in con.execute(
        "SELECT name_norm FROM sunbiz_entity WHERE fetched_at >= ?", (cutoff,))}
    todo, seen = [], set()
    for n in names:
        k = norm_entity(n)
        if k and k not in fresh and k not in seen and is_entity(n):
            seen.add(k)
            todo.append(n)
    done = found = unreachable = 0
    for n in todo[:batch]:
        rec = lookup(n, fetch)
        if rec is None:
            unreachable += 1
            continue
        con.execute(
            "INSERT OR REPLACE INTO sunbiz_entity VALUES (?,?,?,?,?,?,?)",
            (norm_entity(n), 1 if rec["found"] else 0, rec.get("doc_number"), rec.get("status"),
             json.dumps(rec.get("officers") or []), rec.get("agent"),
             datetime.now().isoformat(timespec="seconds")))
        done += 1
        found += 1 if rec["found"] else 0
    con.commit()
    return {"looked_up": done, "found": found, "unreachable": unreachable,
            "remaining": max(0, len(todo) - batch)}


def officers_by_name(con: sqlite3.Connection, names: list[str]) -> dict[str, list[dict]]:
    """Cached officers for these owner names, keyed by norm_entity(name).
    Reads only; an absent cache table simply means nothing is resolved yet."""
    try:
        rows = con.execute("SELECT name_norm, officers FROM sunbiz_entity WHERE found = 1").fetchall()
    except sqlite3.OperationalError:
        return {}
    want = {norm_entity(n) for n in names}
    return {r[0]: json.loads(r[1] or "[]") for r in rows if r[0] in want}


def pending(con: sqlite3.Connection, names: list[str]) -> int:
    """Entity owner names in this list with no cached registry record."""
    try:
        have = {r[0] for r in con.execute("SELECT name_norm FROM sunbiz_entity")}
    except sqlite3.OperationalError:
        have = set()
    return len({norm_entity(n) for n in names if is_entity(n)} - have - {""})
