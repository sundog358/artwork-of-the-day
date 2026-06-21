# Linked Art JSON Schemas (vendored)

These JSON Schema files are vendored **unmodified** from the official Linked Art
validator:

- Source: https://github.com/linked-art/json-validator (`schema/` directory, `master`)
- License: Apache-2.0 (© Linked Art community / J. Paul Getty Trust et al.)

They are used by [`../validate_linked_art.py`](../validate_linked_art.py) to
validate the records this app emits at `/object`, `/visual`, `/person`,
`/place`, and `/group` against the official Linked Art 1.0 model. We vendor them
(rather than fetch at runtime) so validation is reproducible offline and can gate
a release.

`core.json` holds the shared `$defs`; the per-type schemas (`object`, `person`,
`place`, `group`, `image` = VisualItem, `digital`) reference it via relative
`$ref`. To refresh:

```
gh api repos/linked-art/json-validator/contents/schema/<name>.json?ref=master \
  --jq '.content' | base64 -d > linked_art_schema/<name>.json
```
