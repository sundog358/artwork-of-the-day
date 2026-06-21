"""
Phase 10 – Items 3 and 5:

  Item 3: Conflict Dossier Artifact
    build_conflict_dossier()        — build a structured dossier for one contradictory claim
    build_run_conflict_dossiers()   — collect dossiers for all contradicted claims in a run
    write_conflict_dossier_artifact() — write {slug}_conflict_dossier.json to disk

  Item 5: Benchmarked Calibration Workflow for Adjudication Thresholds
    calibrate_adjudication_thresholds()   — grid-search entailment/contradiction thresholds on gold set
    save_adjudication_profile_thresholds() — persist locked profile thresholds JSON
"""

from __future__ import annotations

import json
import os
import re
from datetime import date
from typing import Any, Dict, List, Optional, Tuple


# ──────────────────────────────────────────────────────────────────────────────
# Recency helpers
# ──────────────────────────────────────────────────────────────────────────────

_YEAR_RE = re.compile(r"\b(1[89]\d{2}|20\d{2})\b")

_TODAY_YEAR = date.today().year


def _recency_risk_from_year(year: int, current_year: int = _TODAY_YEAR) -> str:
    age = current_year - year
    if age <= 5:
        return "low"
    if age <= 15:
        return "medium"
    return "high"


def _recency_risk_from_date_string(date_str: str) -> str:
    """Best-effort recency risk from an ISO-date or year string."""
    if not date_str:
        return "unknown"
    m = _YEAR_RE.search(str(date_str))
    if m:
        year = int(m.group(1))
        return _recency_risk_from_year(year)
    return "unknown"


def _overall_recency_risk(parallel_statements: List[Dict[str, Any]]) -> str:
    """Aggregate recency risk: if any statement is high, result is high; etc."""
    risks = [_recency_risk_from_date_string(s.get("retrieved_date", "")) for s in parallel_statements]
    if "high" in risks:
        return "high"
    if "medium" in risks:
        return "medium"
    if risks and all(r == "low" for r in risks):
        return "low"
    return "unknown"


# ──────────────────────────────────────────────────────────────────────────────
# Item 3 – Conflict Dossier
# ──────────────────────────────────────────────────────────────────────────────

def build_conflict_dossier(
    claim_text: str,
    provenance_entry: Dict[str, Any],
    parallel_statements: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Build a structured conflict dossier for a single claim.

    Parameters
    ----------
    claim_text:
        The atomic claim text that has a contradicted (or otherwise flagged) verdict.
    provenance_entry:
        The sentence-level provenance dict from the chapter provenance map.  Expected
        keys: ``verdict``, ``atomic_claims``, ``citation_ids``.
    parallel_statements:
        Optional list of parallel statements (from Wikidata or source evidence) that
        disagree with each other or with the claim.  Each entry may include keys:
        ``text``, ``rank``, ``source_url``, ``citation_id``, ``retrieved_date``,
        ``qualifiers``.  When omitted or empty, the dossier records an empty list.

    Returns
    -------
    dict with keys:
        claim_text, verdict, parallel_statements (enriched),
        sources (deduplicated URLs), recency_risk, qualifier_context,
        citation_ids, generated_on.
    """
    stmts = list(parallel_statements or [])
    verdict = str(provenance_entry.get("verdict", "unknown"))

    enriched_stmts: List[Dict[str, Any]] = []
    seen_urls: List[str] = []
    seen_url_set: set = set()

    for stmt in stmts:
        url = str(stmt.get("source_url", "") or "")
        recency = _recency_risk_from_date_string(str(stmt.get("retrieved_date", "") or ""))
        enriched = {
            "text": str(stmt.get("text", "") or ""),
            "rank": str(stmt.get("rank", "normal") or "normal"),
            "source_url": url,
            "citation_id": str(stmt.get("citation_id", "") or ""),
            "retrieved_date": str(stmt.get("retrieved_date", "") or ""),
            "recency_risk": recency,
            "qualifier_context": dict(stmt.get("qualifiers", {}) or {}),
        }
        enriched_stmts.append(enriched)
        if url and url not in seen_url_set:
            seen_url_set.add(url)
            seen_urls.append(url)

    overall_recency = _overall_recency_risk(enriched_stmts) if enriched_stmts else "unknown"

    return {
        "claim_text": claim_text,
        "verdict": verdict,
        "parallel_statements": enriched_stmts,
        "sources": seen_urls,
        "recency_risk": overall_recency,
        "qualifier_context": {},
        "citation_ids": list(provenance_entry.get("citation_ids", []) or []),
        "generated_on": date.today().isoformat(),
    }


def build_run_conflict_dossiers(
    provenance_map: List[Dict[str, Any]],
    parallel_statements_by_sentence: Optional[Dict[str, List[Dict[str, Any]]]] = None,
) -> List[Dict[str, Any]]:
    """Build conflict dossiers for every contradicted claim in a full provenance map.

    Parameters
    ----------
    provenance_map:
        List of sentence-level provenance dicts (one per sentence).  Each entry
        should have a ``verdict`` key and an ``atomic_claims`` list.
    parallel_statements_by_sentence:
        Optional mapping of ``sentence_id -> [parallel_statement, ...]``.  When
        not supplied, each dossier will have an empty ``parallel_statements`` list.

    Returns
    -------
    List of dossier dicts, one per contradicted sentence.
    """
    if not provenance_map:
        return []

    lookup = parallel_statements_by_sentence or {}
    dossiers: List[Dict[str, Any]] = []

    for entry in provenance_map:
        if not isinstance(entry, dict):
            continue
        verdict = str(entry.get("verdict", ""))
        if verdict != "contradicted":
            continue
        sentence_id = str(entry.get("sentence_id", "") or "")
        sentence_text = str(entry.get("sentence_text", "") or "")
        claim_text = sentence_text

        # Prefer the first contradicted atomic claim text if available
        atomic_claims = entry.get("atomic_claims", []) or []
        for ac in atomic_claims:
            if isinstance(ac, dict) and ac.get("verdict") == "contradicted":
                claim_text = str(ac.get("claim_text", sentence_text) or sentence_text)
                break

        parallel = lookup.get(sentence_id, [])
        dossier = build_conflict_dossier(
            claim_text=claim_text,
            provenance_entry=entry,
            parallel_statements=parallel,
        )
        dossiers.append(dossier)

    return dossiers


def write_conflict_dossier_artifact(
    dossiers: List[Dict[str, Any]],
    output_dir: str,
    slug: str,
) -> str:
    """Write a conflict dossier artifact JSON file.

    File name: ``{slug}_conflict_dossier.json``.

    Returns the absolute path of the written file.
    """
    file_name = f"{slug}_conflict_dossier.json"
    output_path = os.path.join(output_dir, file_name)
    payload = {
        "slug": slug,
        "contradicted_claim_count": len(dossiers),
        "generated_on": date.today().isoformat(),
        "dossiers": dossiers,
    }
    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
    return output_path


# ──────────────────────────────────────────────────────────────────────────────
# Item 5 – Adjudication Threshold Calibration
# ──────────────────────────────────────────────────────────────────────────────

# Default search grid for threshold calibration
_DEFAULT_MIN_ENTAILMENT_GRID = [40.0, 45.0, 50.0, 55.0, 60.0, 65.0]
_DEFAULT_CONTRADICTION_GRID  = [25.0, 30.0, 35.0, 40.0, 45.0]

# Fallback defaults when no gold set is available
DEFAULT_ADJUDICATION_THRESHOLDS: Dict[str, Dict[str, float]] = {
    "draft":       {"min_entailment_percent": 45.0, "contradiction_threshold_percent": 30.0},
    "research":    {"min_entailment_percent": 55.0, "contradiction_threshold_percent": 35.0},
    "publication": {"min_entailment_percent": 62.0, "contradiction_threshold_percent": 40.0},
}


def _negation_in_prefix(text: str, match_start: int, window: int = 40) -> bool:
    """True if a negation word appears in the ``window`` chars before ``match_start``."""
    prefix = text[max(0, match_start - window): match_start].lower()
    return bool(re.search(
        r"\b(not|no |never |did not|does not|was not|were not|is not|are not|incorrect|wrong|false)\b",
        prefix,
    ))


def _predict_verdict(
    claim: str,
    evidence: str,
    min_entailment_percent: float,
    contradiction_threshold_percent: float,
) -> str:
    """Heuristic NLI verdict prediction for calibration purposes.

    Uses lexical token overlap and explicit negation/year-mismatch signals.
    Returns one of: 'supported', 'contradicted', 'neutral'.
    """
    c_lower = claim.lower()
    e_lower = evidence.lower()

    # Stop words to exclude from overlap
    stop = {
        "the", "a", "an", "was", "were", "is", "are", "in", "of", "to",
        "and", "that", "it", "be", "been", "by", "for", "on", "at", "as",
        "its", "this", "with", "from", "or", "but", "not", "did",
    }
    c_tokens = set(re.findall(r"\b\w+\b", c_lower)) - stop
    e_tokens = set(re.findall(r"\b\w+\b", e_lower)) - stop

    if not c_tokens or not e_tokens:
        return "neutral"

    overlap = len(c_tokens & e_tokens) / max(len(c_tokens), 1)

    # Year mismatch check — if both have years and they differ → contradiction signal
    c_years = set(re.findall(r"\b(1[4-9]\d{2}|20\d{2})\b", claim))
    e_years = set(re.findall(r"\b(1[4-9]\d{2}|20\d{2})\b", evidence))
    year_mismatch = bool(c_years and e_years and c_years.isdisjoint(e_years))

    # Negation in evidence ("not the Xth", "did not", "was not X") at key claim tokens
    negation_present = bool(re.search(
        r"\b(not |no |never |did not|does not|was not|were not|is not|are not|incorrect|wrong|false)\b",
        e_lower,
    ))

    # Determine entailment and contradiction percentages (heuristic)
    if year_mismatch or (negation_present and overlap >= 0.25):
        contradiction_pct = 65.0
        entailment_pct = 10.0
    elif negation_present and overlap < 0.25:
        contradiction_pct = 40.0
        entailment_pct = 15.0
    elif overlap >= 0.40:
        entailment_pct = 70.0
        contradiction_pct = 10.0
    elif overlap >= 0.25:
        entailment_pct = 55.0
        contradiction_pct = 15.0
    else:
        entailment_pct = 20.0
        contradiction_pct = 20.0

    # Apply thresholds
    if contradiction_pct >= contradiction_threshold_percent and contradiction_pct > entailment_pct + 5.0:
        return "contradicted"
    if overlap >= 0.20 and entailment_pct >= min_entailment_percent:
        return "supported"
    return "neutral"


def _evaluate_thresholds(
    cases: List[Dict[str, Any]],
    min_entailment_percent: float,
    contradiction_threshold_percent: float,
) -> Tuple[float, float, Dict[str, int]]:
    """Evaluate threshold pair on gold cases.  Returns (accuracy, f1, counts)."""
    tp = fp = tn = fn = 0
    for case in cases:
        claim = str(case.get("claim", "") or "")
        evidence = str(case.get("evidence_snippet", "") or "")
        expected = str(case.get("expected_verdict", "neutral") or "neutral")
        predicted = _predict_verdict(
            claim, evidence, min_entailment_percent, contradiction_threshold_percent
        )
        # Binary: "supported" vs "not supported" (contradicted/neutral)
        pos_pred = predicted == "supported"
        pos_exp  = expected  == "supported"
        if pos_pred and pos_exp:
            tp += 1
        elif pos_pred and not pos_exp:
            fp += 1
        elif not pos_pred and not pos_exp:
            tn += 1
        else:
            fn += 1

    total = tp + fp + tn + fn
    accuracy = round((tp + tn) / total, 4) if total > 0 else 0.0
    precision_val = round(tp / (tp + fp), 4) if (tp + fp) > 0 else 0.0
    recall_val    = round(tp / (tp + fn), 4) if (tp + fn) > 0 else 0.0
    denom = precision_val + recall_val
    f1 = round(2 * precision_val * recall_val / denom, 4) if denom > 0 else 0.0
    counts = {"tp": tp, "fp": fp, "tn": tn, "fn": fn}
    return accuracy, f1, counts


def calibrate_adjudication_thresholds(
    gold_set_path: str,
    min_entailment_grid: Optional[List[float]] = None,
    contradiction_grid: Optional[List[float]] = None,
) -> Dict[str, Any]:
    """Grid-search adjudication thresholds on a gold set and return locked profile defaults.

    Parameters
    ----------
    gold_set_path:
        Path to a JSON file with ``cases`` array.  Each case must have:
        ``claim``, ``evidence_snippet``, ``expected_verdict``.
    min_entailment_grid:
        List of ``min_entailment_percent`` values to try.
    contradiction_grid:
        List of ``contradiction_threshold_percent`` values to try.

    Returns
    -------
    Dict with keys:
        benchmark_path, generated_on, combinations_tested,
        best (thresholds + metrics + composite),
        locked_profile_thresholds.
    """
    try:
        with open(gold_set_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception as exc:
        raise ValueError(f"Cannot load gold set from '{gold_set_path}': {exc}") from exc

    cases = data.get("cases", [])
    if not cases:
        raise ValueError(f"Gold set at '{gold_set_path}' has no cases.")

    ent_grid = list(min_entailment_grid or _DEFAULT_MIN_ENTAILMENT_GRID)
    con_grid = list(contradiction_grid or _DEFAULT_CONTRADICTION_GRID)

    best: Optional[Dict[str, Any]] = None
    combinations = 0

    for ent in ent_grid:
        for con in con_grid:
            combinations += 1
            accuracy, f1, counts = _evaluate_thresholds(cases, ent, con)
            composite = round(0.7 * f1 + 0.3 * accuracy, 4)
            candidate = {
                "thresholds": {
                    "min_entailment_percent": ent,
                    "contradiction_threshold_percent": con,
                },
                "metrics": {
                    "accuracy": accuracy,
                    "f1": f1,
                    "tp": counts["tp"],
                    "fp": counts["fp"],
                    "tn": counts["tn"],
                    "fn": counts["fn"],
                },
                "composite": composite,
            }
            if best is None or composite > float(best["composite"]):
                best = candidate

    if best is None:
        raise ValueError("No threshold combination evaluated.")

    best_ent = float(best["thresholds"]["min_entailment_percent"])
    best_con = float(best["thresholds"]["contradiction_threshold_percent"])

    locked: Dict[str, Dict[str, float]] = {
        "draft": {
            "min_entailment_percent": round(max(0.0, best_ent - 8.0), 2),
            "contradiction_threshold_percent": round(max(0.0, best_con - 6.0), 2),
        },
        "research": {
            "min_entailment_percent": round(best_ent, 2),
            "contradiction_threshold_percent": round(best_con, 2),
        },
        "publication": {
            "min_entailment_percent": round(min(100.0, best_ent + 6.0), 2),
            "contradiction_threshold_percent": round(min(100.0, best_con + 5.0), 2),
        },
    }

    return {
        "benchmark_path": gold_set_path,
        "generated_on": date.today().isoformat(),
        "case_count": len(cases),
        "combinations_tested": combinations,
        "best": best,
        "locked_profile_thresholds": locked,
    }


def save_adjudication_profile_thresholds(
    path: str,
    thresholds: Dict[str, Dict[str, float]],
) -> None:
    """Persist locked adjudication profile thresholds to a JSON file."""
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(thresholds, fh, indent=2, ensure_ascii=False)
