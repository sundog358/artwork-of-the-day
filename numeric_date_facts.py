"""Deterministic numeric/date fact-check (ported in spirit from the reference
repos' phase8_authenticity verifier, adapted for structured Wikidata facts).

The idea: any year, count, percentage, or dimension that appears in a generated
sentence must also appear in the evidence (the facts the sentence cited). A
number that isn't in the evidence is very likely a hallucination — exactly the
kind of confident-but-wrong detail that erodes trust in an "AI wrote this"
article. This runs offline, no API needed.
"""
import re

# Matches integers, decimals, and comma-grouped numbers: 1503, 79.4, 1,200
_NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


def extract_numbers(text):
    """Return the set of normalized numeric tokens in a piece of text."""
    return {m.replace(",", "") for m in _NUM_RE.findall(text or "")}


def _evidence_years(numbers):
    """Four-digit year-like values from a set of normalized number strings."""
    return {int(n) for n in numbers if len(n) == 4 and n.isdigit()}


def unverified_numbers(sentence, evidence):
    """Numbers asserted in `sentence` that are not supported by `evidence`.

    A year ending in 0 (e.g. "1840") is allowed to match any evidence year in
    that decade, so natural phrasing like "in the 1840s" isn't flagged when the
    evidence says 1842.
    """
    ev = extract_numbers(evidence)
    ev_years = _evidence_years(ev)
    missing = []
    for n in extract_numbers(sentence):
        if n in ev:
            continue
        if n.isdigit() and len(n) == 4 and n.endswith("0"):
            decade = int(n)
            if any(decade <= y <= decade + 9 for y in ev_years):
                continue
        missing.append(n)
    return missing
