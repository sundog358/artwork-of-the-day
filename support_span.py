"""Lexical support-span alignment (ported in spirit from the reference repos'
support-span/lexical-overlap check).

For each generated sentence, measure how much of its content vocabulary is
actually present in the evidence it cited. Near-zero overlap means the sentence
is decorated with a citation but not really supported by it — block those. Weak
overlap is a softer signal — warn but allow. This catches the "plausible prose,
wrong source" failure mode without any model call.

An engaging blurb necessarily adds narrative/connective words not present in dry
facts, so we hard-block only *near-zero* overlap (the sentence cited a fact that
has essentially nothing to do with it) and treat the rest as a soft warning. The
strict anti-hallucination guard is the separate numeric/date check, which blocks
any fabricated number outright.
"""
import re

BLOCK_FLOOR = 0.05  # below this = the cited fact is unrelated → block
WARN_FLOOR = 0.35   # below this = weak support → warn but allow

# Function words carry no grounding signal — exclude them from the overlap.
_STOP = set(
    """a an and are as at be been being but by for from had has have he her him his
    in into is it its of on or our she that the their them they this to was were
    what when where which who whom whose will with you your we us i not no""".split()
)

_WORD_RE = re.compile(r"[a-z0-9]+")


def content_tokens(text):
    """Lowercased, stopword-stripped content tokens (length > 1)."""
    return {w for w in _WORD_RE.findall((text or "").lower()) if w not in _STOP and len(w) > 1}


def overlap_score(sentence, evidence):
    """Fraction of the sentence's content tokens that appear in the evidence.

    Returns 1.0 for a sentence with no content tokens (nothing to ground).
    """
    s = content_tokens(sentence)
    if not s:
        return 1.0
    e = content_tokens(evidence)
    return len(s & e) / len(s)
