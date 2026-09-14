"""One buyer behind several LLCs.

`top_owner_pct` counts units under a single owner name, which an assembler can
defeat by holding each unit in its own entity. Shared mailing address is the
existing counter-signal and it is a good one, but it is a single exact-match
test: it misses a buyer using a registered-agent address for some entities and a
home address for others, and it says nothing about entities whose names are
plainly a series.

This clusters owners transitively instead. If FLAGLER HOLDINGS I LLC and FLAGLER
HOLDINGS II LLC share a name series, and FLAGLER HOLDINGS II LLC shares a mailing
address with BRICKELL 27 LLC, then all three are one group -- which neither test
finds alone.

The governing constraint is stated in norm.normalize_owner and it applies double
here: **a false merge is worse than a missed one**, because concentration is the
number this whole tool is ranked on and a bad cluster fabricates it. So:

  * every edge is one of a small number of named, auditable rules
  * each rule is conservative to the point of leaving real links on the table
  * the evidence for every edge is kept and reported, so a cluster can be
    disbelieved on its specifics rather than on principle

Sunbiz -- Florida's corporate registry, which names officers and registered
agents -- is the obvious next edge type and plugs in at `EDGE_RULES` without
touching anything else. It is not wired here because it needs a bulk file this
codebase does not yet download.
"""
from __future__ import annotations

import re
import sqlite3
from collections import defaultdict

# Legal form only. Words like HOLDINGS, PROPERTIES and GROUP are NOT stripped:
# they are part of the name and carry most of its distinctiveness. Removing them
# turns "FLAGLER HOLDINGS II LLC" into "FLAGLER", which would then match any
# unrelated owner with FLAGLER in the name -- a street, a neighbourhood, and a
# dozen firms.
_SUFFIX_RE = re.compile(
    r"\b(LLC|L L C|LC|INC|CORP|CORPORATION|CO|COMPANY|LP|LLP|LLLP|LTD|"
    r"PLLC|PA|TRUSTEE)\b")
_SERIES_RE = re.compile(r"\b(\d{1,4}|[IVX]{1,5}|[A-Z])\b\s*$")

# A stem this short is not evidence of anything -- "SOBE" or "MIA" would sweep
# unrelated owners into one cluster.
MIN_STEM_TOKENS = 2
MIN_STEM_CHARS = 9

# A mailing address shared by this many DISTINCT owner names is a mail drop or a
# management company, not a buyer. Above it, the edge is dropped.
MAX_NAMES_PER_ADDRESS = 12


def stem(owner_norm: str) -> str:
    """The comparable core of an entity name: suffixes and a trailing series
    marker removed. Returns '' when what is left is too thin to trust."""
    if not owner_norm:
        return ""
    s = _SUFFIX_RE.sub(" ", owner_norm.upper())
    s = re.sub(r"\s+", " ", s).strip()
    s = _SERIES_RE.sub("", s).strip()
    toks = s.split()
    if len(toks) < MIN_STEM_TOKENS or len(s.replace(" ", "")) < MIN_STEM_CHARS:
        return ""
    return " ".join(toks)


class _Union:
    def __init__(self):
        self.parent: dict[str, str] = {}

    def find(self, x):
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra
        return ra != rb


def cluster(units: list[dict]) -> dict:
    """Group the owners of one building into beneficial owners.

    units: [{owner_norm, owner_addr_norm, is_entity}]
    """
    owners: dict[str, dict] = {}
    for u in units:
        o = (u.get("owner_norm") or "").strip()
        if not o:
            continue
        rec = owners.setdefault(o, {"units": 0, "addrs": set(), "is_entity": 0})
        rec["units"] += 1
        rec["is_entity"] = max(rec["is_entity"], int(u.get("is_entity") or 0))
        a = (u.get("owner_addr_norm") or "").strip()
        if a:
            rec["addrs"].add(a)

    uf = _Union()
    for o in owners:
        uf.find(o)
    edges: list[dict] = []

    # Rule 1 — a mailing address shared by two or more owner names.
    by_addr: dict[str, set] = defaultdict(set)
    for o, rec in owners.items():
        for a in rec["addrs"]:
            by_addr[a].add(o)
    for addr, names in by_addr.items():
        if len(names) < 2:
            continue
        if len(names) > MAX_NAMES_PER_ADDRESS:
            continue          # a mail drop or a management company, not a buyer
        ordered = sorted(names)
        for other in ordered[1:]:
            if uf.union(ordered[0], other):
                edges.append({"rule": "shared_mailing", "evidence": addr,
                              "a": ordered[0], "b": other})

    # Rule 2 — a name series, but only between entities. Two individuals sharing
    # a surname are a family, not a buyer, and merging them would be exactly the
    # false positive normalize_owner refuses to make.
    by_stem: dict[str, set] = defaultdict(set)
    for o, rec in owners.items():
        if not rec["is_entity"]:
            continue
        st = stem(o)
        if st:
            by_stem[st].add(o)
    for st, names in by_stem.items():
        if len(names) < 2:
            continue
        ordered = sorted(names)
        for other in ordered[1:]:
            if uf.union(ordered[0], other):
                edges.append({"rule": "name_series", "evidence": st,
                              "a": ordered[0], "b": other})

    groups: dict[str, dict] = {}
    for o, rec in owners.items():
        root = uf.find(o)
        g = groups.setdefault(root, {"members": [], "units": 0, "is_entity": 0})
        g["members"].append(o)
        g["units"] += rec["units"]
        g["is_entity"] = max(g["is_entity"], rec["is_entity"])

    total = sum(r["units"] for r in owners.values())
    out = []
    for root, g in groups.items():
        members = sorted(g["members"], key=lambda m: (-owners[m]["units"], m))
        out.append({
            "name": members[0],
            "members": members,
            "member_count": len(members),
            "units": g["units"],
            "pct": round(100.0 * g["units"] / total, 2) if total else 0.0,
            "is_entity": bool(g["is_entity"]),
            "evidence": [e for e in edges
                         if uf.find(e["a"]) == root or uf.find(e["b"]) == root],
        })
    out.sort(key=lambda g: -g["units"])
    return {"total_units": total, "distinct_owner_names": len(owners),
            "beneficial_owners": len(out), "groups": out}


def top_beneficial(con: sqlite3.Connection, group_key: str) -> dict:
    """Cluster one building straight from the roll, and say what it changed."""
    units = [dict(r) for r in con.execute(
        "SELECT owner_norm, owner_addr_norm, is_entity FROM nal_condo_unit "
        "WHERE group_key=?", (group_key,))]
    res = cluster(units)
    top = res["groups"][0] if res["groups"] else None
    # The single-name figure the screen scores on, for comparison.
    single = max((sum(1 for u in units if (u.get("owner_norm") or "") == n)
                  for n in {u.get("owner_norm") for u in units if u.get("owner_norm")}),
                 default=0)
    res["single_name_top_units"] = single
    res["single_name_top_pct"] = (round(100.0 * single / res["total_units"], 2)
                                  if res["total_units"] else 0.0)
    res["uplift_pct"] = (round(top["pct"] - res["single_name_top_pct"], 2)
                         if top else 0.0)
    return res
