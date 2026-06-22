"""Validate generated Linked Art records against the OFFICIAL Linked Art JSON
Schemas.

The schemas in `linked_art_schema/` are vendored from linked-art/json-validator
(Apache-2.0 — see linked_art_schema/SOURCE.md). This builds real records from
live Wikidata via `sparql_library` + `linked_art` and asserts each conforms to
its schema, exactly as the upstream `schema-test.py` does (Draft 7 validator,
relative `$ref`s resolved against each schema's `$id`).

Dev tool only — needs `pip install jsonschema` (requirements-dev.txt); the app
does not import it. Run:  python validate_linked_art.py
Exits non-zero if any record fails, so it can gate a release.
"""

import json
import os
import sys

from jsonschema import Draft7Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT7

import article_writer
import linked_art as la
import sparql_library as sl

SCHEMA_DIR = os.path.join(os.path.dirname(__file__), "linked_art_schema")
BASE = "https://example.org"


def _registry():
    resources = []
    for fn in os.listdir(SCHEMA_DIR):
        if not fn.endswith(".json"):
            continue
        with open(os.path.join(SCHEMA_DIR, fn), encoding="utf-8") as fh:
            schema = json.load(fh)
        resources.append((schema["$id"], Resource(contents=schema, specification=DRAFT7)))
    return Registry().with_resources(resources)


def _validator(schema_file, registry):
    with open(os.path.join(SCHEMA_DIR, schema_file), encoding="utf-8") as fh:
        schema = json.load(fh)
    return Draft7Validator(schema, registry=registry)


def _check(name, record, validator):
    errors = sorted(validator.iter_errors(record), key=lambda e: list(e.absolute_path))
    if not errors:
        print(f"  [PASS] {name}")
        return 0
    print(f"  [FAIL] {name} — {len(errors)} error(s):")
    for e in errors[:12]:
        path = "/" + "/".join(str(p) for p in e.absolute_path)
        print(f"      {path}: {e.message}")
    return len(errors)


def build_records():
    """Build one record of each type from live Wikidata (Mona Lisa / Leonardo /
    Paris / Louvre)."""
    aw_qid, ar_qid = "Q12418", "Q762"
    artist_qid = sl.creator_of(aw_qid) or ar_qid
    artwork, artist = sl.build_dossier(aw_qid, artist_qid)
    summary = article_writer.build(artwork, artist, generate=False)
    desc = " ".join(p for s in summary.get("sections", []) for p in s.get("paragraphs", []))
    # Exercise the object's `used_for` activities (provenance / event history).
    artwork["activities"] = [{"kind": "event", "label": "theft", "start": "1911", "end": "1913"}]

    obj = la.object_record(
        artwork,
        artist,
        base=BASE,
        object_uri=f"{BASE}/object/{aw_qid}",
        person_uri=f"{BASE}/person/{artist_qid}",
        artwork_qid=aw_qid,
        artist_qid=artist_qid,
        description=desc,
    )

    depicts = sl.related([(aw_qid, "depicts", "P180")]).get("depicts", [])
    artwork["depicts"] = depicts
    et = sl.classify_entities([d["qid"] for d in depicts if d.get("qid")])
    vis = la.visual_record(
        artwork, visual_uri=f"{BASE}/visual/{aw_qid}", artwork_qid=aw_qid, entity_types=et
    )

    per = la.person_record(
        sl.artist_facts(artist_qid),
        base=BASE,
        person_uri=f"{BASE}/person/{artist_qid}",
        artist_qid=artist_qid,
    )

    plc = la.place_record(sl.place_facts("Q90"), place_uri=f"{BASE}/place/Q90", place_qid="Q90")
    grp = la.group_record(
        sl.group_facts("Q19675"), group_uri=f"{BASE}/group/Q19675", group_qid="Q19675"
    )
    con = la.concept_record(
        sl.concept_facts("Q1474884"),  # Italian Renaissance
        concept_uri=f"{BASE}/concept/Q1474884",
        concept_qid="Q1474884",
    )
    st = la.set_record(sl.group_facts("Q19675"), set_uri=f"{BASE}/set/Q19675", set_qid="Q19675")

    return [
        ("object.json", "HumanMadeObject (Mona Lisa)", obj),
        ("image.json", "VisualItem (depicted subjects)", vis),
        ("person.json", "Person (Leonardo da Vinci)", per),
        ("place.json", "Place (Paris)", plc),
        ("group.json", "Group (Louvre)", grp),
        ("concept.json", "Concept (Italian Renaissance)", con),
        ("set.json", "Set (Louvre collection)", st),
    ]


def main():
    registry = _registry()
    print("Validating Linked Art records against official schemas:\n")
    total = 0
    for schema_file, name, record in build_records():
        total += _check(name, record, _validator(schema_file, registry))
    print()
    if total:
        print(f"FAILED — {total} schema violation(s).")
        sys.exit(1)
    print("All records conform to the official Linked Art JSON Schemas. (10/10)")


if __name__ == "__main__":
    main()
