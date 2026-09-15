"""Extract termination terms from a recorded condominium declaration.

Two questions decide whether a building is a realistic termination play:

  1. What vote does the declaration require to terminate?
  2. Does it contain "Kaufman language" -- a clause incorporating the Florida
     Condominium Act *as amended from time to time*? Under Kaufman v. Shere,
     347 So. 2d 627 (Fla. 3d DCA 1977), that phrasing pulls in later statutory
     amendments, so a declaration requiring unanimity may still be terminable at
     the statutory 80% under FS 718.117.

The third question is the one that decides deals, and it is why this module
reports two separate Kaufman flags rather than one:

  3. Was the Kaufman language in the ORIGINAL recorded declaration, or added
     later BY AMENDMENT?

Florida's Third DCA has ruled against a developer that acquired 183 of 192 units
and then amended the declaration to insert Kaufman language and drop the
threshold from unanimous to 80%. Kaufman added by a bulk owner after the fact is
the fact pattern that lost. So `kaufman_by_amendment` is close to worthless and
must never be collapsed into `kaufman_original`.

Every field comes back with the verbatim snippet it was drawn from. Nothing here
is authority -- it is a reading aid that tells a human where to look.
"""
import re
from dataclasses import dataclass, field

# ── Kaufman language ───────────────────────────────────────────────────────
KAUFMAN_PATTERNS = [
    r"as\s+(?:it\s+|the\s+same\s+|they\s+)?may\s+be\s+amended\s+from\s+time\s+to\s+time",
    r"as\s+(?:it\s+is\s+|the\s+same\s+(?:is|are)\s+)?amended\s+from\s+time\s+to\s+time",
    r"as\s+(?:said\s+|such\s+)?(?:act|chapter|statute)s?\s+(?:may\s+be\s+)?amended\s+from\s+time\s+to\s+time",
    r"and\s+as\s+(?:hereafter|thereafter)\s+amended",
]
_KAUFMAN_RE = re.compile("|".join(KAUFMAN_PATTERNS), re.I | re.S)

# The clause only matters if it is attached to the Act, not to, say, the rules
# and regulations. Look for a statutory reference near the phrase.
_ACT_NEAR_RE = re.compile(
    r"(condominium\s+act|chapter\s+718|fla\.?\s*stat|florida\s+statutes?)", re.I)

# ── amendment detection ────────────────────────────────────────────────────
_AMENDMENT_TITLE_RE = re.compile(
    r"\b(amendment|amended\s+and\s+restated|certificate\s+of\s+amendment|"
    r"first\s+amendment|second\s+amendment)\b.{0,80}?\bdeclaration\b", re.I | re.S)
_ORIGINAL_TITLE_RE = re.compile(
    r"\bdeclaration\s+of\s+condominium\b(?!.{0,40}\bamend)", re.I | re.S)

# ── other terms that kill a deal ───────────────────────────────────────────
# Three more findings, because the threshold and Kaufman are not the only things
# that stop an assembly. Each needs its own context test: the bare phrase appears
# in declarations that do not impose the thing it names.

# A right of first refusal routes every unit purchase through the board, which
# throttles quiet assembly whatever the termination vote says.
_ROFR_RE = re.compile(
    r"\bright\s+of\s+first\s+refusal\b|\bfirst\s+right\s+of\s+refusal\b|"
    r"\bright\s+to\s+(?:purchase|acquire)\s+(?:the\s+)?(?:unit|interest)\b", re.I)
_ROFR_CONTEXT_RE = re.compile(
    r"\b(sale|sell|transfer|convey\w*|lease|assign\w*|purchas\w*)\b", re.I)

# A recreation lease or leasehold land means the fee under the building is not
# necessarily yours to redevelop -- it can survive termination entirely.
_LEASE_RE = re.compile(
    r"\brecreation(?:al)?\s+(?:facilit\w+\s+)?lease\b|\bground\s+lease\b|"
    r"\blong[- ]term\s+lease\b|\bleasehold\s+(?:interest|estate)\b", re.I)
_LEASE_CONTEXT_RE = re.compile(
    r"\b(lessor|lessee|demised|rent|term\s+of\s+(?:the\s+)?lease|99\s+years?)\b", re.I)

# A 55+ covenant changes both the buyer pool and the politics of relocation.
_AGE_RE = re.compile(
    r"\bhousing\s+for\s+older\s+persons\b|"
    r"\b(?:fifty[- ]five|55)\s*(?:\(\s*55\s*\))?\s*years?\s+(?:of\s+age\s+)?or\s+(?:older|over)\b|"
    r"\badult\s+(?:only\s+)?community\b", re.I)


# ── termination threshold ──────────────────────────────────────────────────
_WORD_NUM = {
    "fifty": 50, "sixty": 60, "sixty-six": 66, "seventy": 70, "seventy-five": 75,
    "eighty": 80, "eighty-five": 85, "ninety": 90, "ninety-five": 95,
    "one hundred": 100, "hundred": 100,
}
_PCT_RE = re.compile(
    r"(?P<num>\d{1,3}(?:\.\d+)?)\s*(?:%|per\s?cent(?:um)?|percent)", re.I)
_WORDPCT_RE = re.compile(
    r"\b(?P<word>fifty|sixty-six|sixty|seventy-five|seventy|eighty-five|eighty|"
    r"ninety-five|ninety|one hundred|hundred)\s*(?:\(\s*\d{1,3}\s*\)\s*)?"
    r"(?:%|per\s?cent(?:um)?|percent)", re.I)
_UNANIMOUS_RE = re.compile(
    r"\b(unanimous(?:ly)?|all\s+(?:of\s+)?the\s+(?:unit\s+)?owners|"
    r"all\s+(?:of\s+)?the\s+(?:record\s+)?owners|one\s+hundred\s+per\s?cent)\b", re.I)

# Sentences that are actually about terminating the condominium.
_TERM_CONTEXT_RE = re.compile(
    r"\b(terminat\w+|remov\w+\s+from\s+(?:the\s+)?condominium|"
    r"withdraw\w*\s+from\s+(?:the\s+)?condominium)\b", re.I)


def _windows(text, pattern, before=420, after=420):
    for m in pattern.finditer(text):
        yield text[max(0, m.start() - before): m.end() + after], m


def _clean(s):
    return re.sub(r"\s+", " ", s).strip()


@dataclass
class DeclarationFindings:
    termination_threshold: str | None = None
    threshold_pct: float | None = None
    threshold_snippet: str | None = None
    kaufman_present: bool = False
    kaufman_original: bool | None = None
    kaufman_by_amendment: bool | None = None
    kaufman_snippets: list[str] = field(default_factory=list)
    doc_type: str | None = None          # original | amendment | unknown
    rofr: bool = False
    rofr_snippet: str | None = None
    leasehold: bool = False
    leasehold_snippet: str | None = None
    age_restricted: bool = False
    age_snippet: str | None = None
    confidence: str = "low"              # low | medium | high
    warnings: list[str] = field(default_factory=list)

    def as_row(self):
        return {
            "termination_threshold": self.termination_threshold,
            "kaufman_original": None if self.kaufman_original is None else int(self.kaufman_original),
            "kaufman_by_amendment": None if self.kaufman_by_amendment is None else int(self.kaufman_by_amendment),
            "stage2_notes": self.note(),
        }

    def as_doc_row(self):
        """One reviewed document, kept whole. The target table holds the
        SYNTHESIS across documents; this holds what each one actually said."""
        return {
            "doc_type": self.doc_type,
            "termination_threshold": self.termination_threshold,
            "threshold_pct": self.threshold_pct,
            "kaufman_present": int(self.kaufman_present),
            "rofr": int(self.rofr),
            "leasehold": int(self.leasehold),
            "age_restricted": int(self.age_restricted),
            "confidence": self.confidence,
            "notes": self.note(),
        }

    def note(self):
        bits = [f"doc={self.doc_type or 'unknown'}", f"confidence={self.confidence}"]
        if self.threshold_snippet:
            bits.append(f'threshold ctx: "{self.threshold_snippet[:220]}"')
        if self.kaufman_snippets:
            bits.append(f'kaufman ctx: "{self.kaufman_snippets[0][:220]}"')
        for label, on in (("ROFR", self.rofr), ("leasehold", self.leasehold),
                          ("55+", self.age_restricted)):
            if on:
                bits.append(label)
        bits += self.warnings
        return " | ".join(bits)


def classify_document(text: str) -> str:
    head = text[:4000]
    if _AMENDMENT_TITLE_RE.search(head):
        return "amendment"
    if _ORIGINAL_TITLE_RE.search(head):
        return "original"
    if _AMENDMENT_TITLE_RE.search(text):
        return "amendment"
    return "unknown"


def find_kaufman(text: str):
    """Return (present, snippets). Only counts the phrase when a reference to the
    Condominium Act sits near it -- 'as amended from time to time' attached to
    the association's rules is not Kaufman language."""
    snippets = []
    for window, m in _windows(text, _KAUFMAN_RE, 500, 300):
        if _ACT_NEAR_RE.search(window):
            snippets.append(_clean(window))
    return bool(snippets), snippets[:5]


def find_threshold(text: str):
    """Return (label, pct, snippet) for the termination vote requirement."""
    best = None  # (specificity, pct, label, snippet)
    for window, m in _windows(text, _TERM_CONTEXT_RE, 300, 700):
        w = _clean(window)
        pcts = []
        for pm in _PCT_RE.finditer(w):
            try:
                pcts.append((float(pm.group("num")), pm.group(0)))
            except ValueError:
                pass
        for wm in _WORDPCT_RE.finditer(w):
            key = wm.group("word").lower()
            if key in _WORD_NUM:
                pcts.append((float(_WORD_NUM[key]), wm.group(0)))
        # Vote thresholds live in 50-100; ignore stray percentages such as
        # undivided-interest shares (0.16894%) or interest rates.
        pcts = [(p, raw) for p, raw in pcts if 50 <= p <= 100]
        if pcts:
            pct, raw = max(pcts, key=lambda x: x[0])
            cand = (2, pct, f"{pct:g}%", w)
            if not best or cand[:2] > best[:2]:
                best = cand
        elif _UNANIMOUS_RE.search(w):
            cand = (1, 100.0, "100% (unanimous)", w)
            if not best or cand[:2] > best[:2]:
                best = cand
    if not best:
        return None, None, None
    return best[2], best[1], best[3]


def _flag(text, pattern, context, before=300, after=400):
    """True plus the snippet, only where a supporting term sits nearby. The bare
    phrase shows up in declarations that do not impose the thing it names."""
    for window, _m in _windows(text, pattern, before, after):
        if context is None or context.search(window):
            return True, _clean(window)
    return False, None


def analyze(text: str) -> DeclarationFindings:
    """Read a declaration (or an amendment to one) and report its termination terms."""
    f = DeclarationFindings()
    if not text or len(text.strip()) < 200:
        f.warnings.append("document text is empty or too short to read")
        return f

    f.doc_type = classify_document(text)
    label, pct, snip = find_threshold(text)
    f.termination_threshold, f.threshold_pct, f.threshold_snippet = label, pct, snip

    f.rofr, f.rofr_snippet = _flag(text, _ROFR_RE, _ROFR_CONTEXT_RE)
    f.leasehold, f.leasehold_snippet = _flag(text, _LEASE_RE, _LEASE_CONTEXT_RE)
    f.age_restricted, f.age_snippet = _flag(text, _AGE_RE, None)

    present, snippets = find_kaufman(text)
    f.kaufman_present = present
    f.kaufman_snippets = snippets
    if present:
        if f.doc_type == "amendment":
            f.kaufman_original, f.kaufman_by_amendment = False, True
            f.warnings.append(
                "Kaufman language found in an AMENDMENT, not the original declaration -- "
                "under the 3d DCA ruling this is the fact pattern that failed")
        elif f.doc_type == "original":
            f.kaufman_original, f.kaufman_by_amendment = True, False
        else:
            f.warnings.append("could not tell whether this is the original declaration "
                              "or an amendment -- Kaufman origin unresolved")
    else:
        f.kaufman_original = f.kaufman_by_amendment = False

    # Confidence: both answers found and the document type is known.
    if label and f.doc_type != "unknown" and (not present or f.kaufman_original is not None):
        f.confidence = "high" if snip else "medium"
    elif label or present:
        f.confidence = "medium"
    if not label:
        f.warnings.append("no termination vote requirement found in the text")
    return f


# ── synthesis across a building's documents ────────────────────────────────

def synthesise(docs: list[dict]) -> dict:
    """The operative terms, from every document reviewed for one building.

    Precedence is not "most recent wins", because the two findings answer
    different questions:

      threshold        the latest AMENDMENT that states one, else the original.
                       An amendment that changes the vote is the operative vote.

      kaufman_original comes from the ORIGINAL declaration and nowhere else. An
                       amendment saying the Act applies as amended from time to
                       time does NOT make it original -- that is precisely the
                       fact pattern the 3d DCA rejected, and reading it as
                       original would turn the tool's central distinction into
                       a rubber stamp.

    The deal-killers (ROFR, leasehold, 55+) are ORed across documents: an
    amendment can add one, and nothing here assumes a later document removed it.
    Repeal has to be read by a human, so a removal is not inferred from silence.
    """
    if not docs:
        return {}
    def year(d):
        return d.get("recorded_year") or 0
    originals = [d for d in docs if d.get("doc_type") == "original"]
    amendments = sorted([d for d in docs if d.get("doc_type") == "amendment"], key=year)
    unknown = [d for d in docs if d.get("doc_type") not in ("original", "amendment")]

    thr = next((d for d in reversed(amendments) if d.get("termination_threshold")), None)
    if thr is None:
        thr = next((d for d in originals if d.get("termination_threshold")), None)
    if thr is None:
        thr = next((d for d in unknown if d.get("termination_threshold")), None)

    orig_k = None
    if originals:
        orig_k = any(bool(d.get("kaufman_present")) for d in originals)
    by_amend = any(bool(d.get("kaufman_present")) for d in amendments) or None
    if by_amend and orig_k:
        by_amend = False          # already original; the amendment adds nothing

    warnings = []
    if not originals:
        warnings.append(
            "No ORIGINAL declaration among the documents reviewed, so whether Kaufman "
            "language was in it is unresolved — which is the question that decides "
            "whether the statutory 80% reaches this building.")
    if unknown:
        warnings.append(
            f"{len(unknown)} document(s) could not be classified as the original or an "
            "amendment; their findings are included but their precedence is a guess.")

    return {
        "termination_threshold": thr.get("termination_threshold") if thr else None,
        "threshold_from": thr.get("doc_type") if thr else None,
        "kaufman_original": None if orig_k is None else int(orig_k),
        "kaufman_by_amendment": None if by_amend is None else int(bool(by_amend)),
        "rofr": int(any(d.get("rofr") for d in docs)),
        "leasehold": int(any(d.get("leasehold") for d in docs)),
        "age_restricted": int(any(d.get("age_restricted") for d in docs)),
        "declaration_docs": len(docs),
        "warnings": warnings,
    }


# ── self-test ──────────────────────────────────────────────────────────────
_SAMPLES = {
    "original_unanimous_kaufman": ("""
        DECLARATION OF CONDOMINIUM OF SEASIDE TOWERS, A CONDOMINIUM
        This Declaration is made pursuant to the Condominium Act, Chapter 718,
        Florida Statutes, as the same may be amended from time to time.
        ARTICLE XVIII. TERMINATION. The condominium may be terminated only upon
        the written consent of all of the unit owners and all record owners of
        liens upon the units.
    """, "100% (unanimous)", True, False),

    "original_80_no_kaufman": ("""
        DECLARATION OF CONDOMINIUM OF BAYVIEW MANOR, A CONDOMINIUM
        Made under the Condominium Act, Chapter 718, Florida Statutes (1979).
        SECTION 22. TERMINATION OF CONDOMINIUM. This condominium may be
        terminated upon the approval of not less than eighty percent (80%) of
        the total voting interests of the Association.
    """, "80%", False, False),

    "amendment_adds_kaufman": ("""
        CERTIFICATE OF AMENDMENT TO THE DECLARATION OF CONDOMINIUM OF
        HARBOR POINTE, A CONDOMINIUM
        The Declaration is hereby amended to provide that it shall be governed by
        the Condominium Act, Chapter 718, Florida Statutes, as amended from time
        to time. Article XX, TERMINATION, is amended to permit termination upon
        approval of eighty percent (80%) of the total voting interests.
    """, "80%", False, True),

    "undivided_interest_noise": ("""
        DECLARATION OF CONDOMINIUM OF PALM COURT, A CONDOMINIUM
        Each unit shall have an undivided 0.16894% interest in the common
        elements. Pursuant to Chapter 718, Florida Statutes.
        TERMINATION. The condominium shall be terminated upon the written
        agreement of ninety percent (90%) of the unit owners.
    """, "90%", False, False),
}


def _selftest():
    ok = True
    for name, (text, want_thr, want_orig, want_amend) in _SAMPLES.items():
        f = analyze(text)
        good = (f.termination_threshold == want_thr
                and f.kaufman_original == want_orig
                and f.kaufman_by_amendment == want_amend)
        ok &= good
        print(f"  [{'PASS' if good else 'FAIL'}] {name}")
        print(f"         threshold={f.termination_threshold!r} (want {want_thr!r}) "
              f"kaufman orig={f.kaufman_original} amend={f.kaufman_by_amendment} "
              f"doc={f.doc_type} conf={f.confidence}")
        if not good:
            print(f"         warnings: {f.warnings}")
    print("\nself-test:", "all passed" if ok else "FAILURES ABOVE")
    return ok


if __name__ == "__main__":
    import sys
    sys.exit(0 if _selftest() else 1)
