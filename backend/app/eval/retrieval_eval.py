"""Retrieval evaluation.

Three checks, none of which need a hand-labelled set (a labelled set can be
added with --gold):

1. Monograph routing: every monograph name, written several realistic ways,
   must resolve to its own monograph; invented and look-alike names must not
   resolve to anything.
2. Field retrieval: for every labelled field of every monograph, a natural
   language query built from the drug name and the field label must bring that
   exact chunk into the top-k of global semantic search (recall@k and MRR).
3. Gold queries (optional): JSON list of
   {"drug": "...", "query": "...", "expected": ["Field label", ...]}; reported
   as hit@k over the scoped + global retrieval used in verification.
"""

import json
import random
from pathlib import Path

from sqlalchemy import select

from app.config import get_settings
from app.db import session_scope
from app.ingest.embeddings import get_embedder
from app.models import Chunk, Document
from app.verification.resolver import resolve
from app.verification.retrieval import monograph_index, retrieve


class _Entity:
    def __init__(self, drug_name: str, query: str):
        self.drug_name, self.dose_value, self.dose_unit = drug_name, None, None
        self.route = self.frequency = self.timing = self.duration = self.indication = None
        self._query = query


def _variants(name: str) -> list[str]:
    return [name, name.lower(), name.upper(), f"{name} 10 mg tablets", f"{name} (oral)", name.replace("-", " ")]


def _lookalikes(name: str, rnd: random.Random) -> list[str]:
    out = [name[:-1] + ("a" if name[-1] != "a" else "e"), name[0] + name[2:] if len(name) > 3 else name + "x", name + "ine"]
    return [o for o in out if o.lower() != name.lower()]


def run_eval(reference_title: str | None, gold_path: Path | None, k: int) -> int:
    settings = get_settings()
    embedder = get_embedder()
    rnd = random.Random(7)
    with session_scope() as db:
        q = select(Document).where(Document.kind == "reference")
        if reference_title:
            q = q.where(Document.title == reference_title)
        refs = db.execute(q).scalars().all()
        if len(refs) != 1:
            print("Specify --reference-title; found:", [r.title for r in refs])
            return 2
        ref = refs[0]
        index = monograph_index(db, ref.id)
        print(f"Reference: {ref.title} ({len(index)} monographs)\n")

        total = ok = 0
        for key, name in index.items():
            for v in _variants(name):
                total += 1
                r = resolve(v, index, settings.fuzzy_name_threshold)
                ok += r.status != "none" and r.key == key
        print(f"1a. name variants resolve to own monograph: {ok}/{total}")
        total = bad = 0
        for key, name in index.items():
            for v in _lookalikes(name, rnd):
                total += 1
                r = resolve(v, index, settings.fuzzy_name_threshold)
                bad += r.status != "none"
        print(f"1b. look-alike names wrongly resolved (want 0): {bad}/{total}")

        fields = db.execute(
            select(Chunk).where(Chunk.document_id == ref.id, Chunk.monograph_key.is_not(None), Chunk.embedding.is_not(None))
        ).scalars().all()
        hits = 0
        rr = 0.0
        for c in fields:
            query = f"{c.monograph} {c.field_label}"
            qvec = embedder.embed_query(query)
            dist = Chunk.embedding.cosine_distance(qvec)
            ids = db.execute(
                select(Chunk.id).where(Chunk.doc_kind == "reference", Chunk.document_id == ref.id).order_by(dist).limit(k)
            ).scalars().all()
            if c.id in ids:
                hits += 1
                rr += 1.0 / (ids.index(c.id) + 1)
        n = len(fields) or 1
        print(f"2.  field retrieval over {len(fields)} fields: recall@{k}={hits / n:.3f}  MRR={rr / n:.3f}")

        if gold_path:
            gold = json.loads(gold_path.read_text())
            g_hits = 0
            for item in gold:
                e = _Entity(item["drug"], item["query"])
                e.indication = item["query"]
                res = resolve(item["drug"], index, settings.fuzzy_name_threshold)
                r = retrieve(db, ref.id, e, res, embedder, settings)
                labels = {(p.chunk.field_label or "").lower() for p in r.passages[: k + 4]}
                g_hits += all(x.lower() in labels for x in item["expected"])
            print(f"3.  gold queries: hit@{k} = {g_hits}/{len(gold)}")
    return 0
