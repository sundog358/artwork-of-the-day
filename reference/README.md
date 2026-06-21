# Reference assets (salvaged)

Concrete bits salvaged from three local reference repos (`articlewriter`,
`non-fiction-book-maker`, `wikidata-explorer`) before they were deleted. The
*patterns* from those repos are already implemented in this app (see
[../WIKIDATA.md](../WIKIDATA.md) §5–§7). These files are kept only as starting
points for the deferred "next phase" features below — nothing here is wired into
the running app.

> Provenance: these came from the project owner's own reference projects. They
> are not used at runtime; treat them as design references, not dependencies.

## conflict-aware-synthesis/
For a future feature where the article presents **parallel claims when Wikidata
disagrees** (e.g. a disputed creation date) instead of silently picking one.
- `conflict_dossier.py` — how the book-maker detected conflicting preferred
  statements and packaged them for the writer.
- `major_claim_rules.json` — keyword/year heuristics for flagging "major" claims
  that warrant corroboration.

## verifier-calibration/
For tuning our verifier thresholds (`support_span.BLOCK_FLOOR/WARN_FLOOR`, the
numeric/date checks) against a labelled gold set instead of hand-picked numbers.
These are **US-history / book-domain** examples — useful as a *format template*;
we'd build a small art-domain gold set of our own.
- `*_benchmark.json`, `*_gold_set.json`, `*_thresholds.json` — labelled
  claim/evidence cases and the threshold configs they calibrated.

## streaming/
For **streaming the article generation** to the browser (so a long blog post
appears progressively instead of after a pause).
- `streaming-pipeline-plan.md`, `summary-streaming-analysis.md` — design notes.

## wikidata-rest-guide/
- `WIKIDATA_REST_API_INTEGRATION_GUIDE.md` — the full 33-endpoint Wikibase REST
  reference. The parts we use are already distilled into `../WIKIDATA.md` §3;
  this is the deep version if we need an endpoint we haven't covered.
