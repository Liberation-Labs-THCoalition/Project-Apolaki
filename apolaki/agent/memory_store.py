"""Apolaki Memory Store — sqlite-vec backed with KG + TGS verification.

Full Mnemosyne stack minus oracle-memory (no cache geometry):
  CMA (compression/consolidation) → KG (entity graph) → TGS (verification) → Wiki

Uses sqlite-vec for vector search instead of pgvector.
Runs entirely local on the Mac Studio.

2026-09-23 audit (Vera; backup memory_store.py.bak-20260923-audit):
  - memories.archived / archived_at / archive_reason columns (migrated in place).
    archive_memory() keeps the row and its embedding blob, removes the row from the
    vec index, and every read path (dense, graph, wiki, list_by_type) filters it.
    unarchive_memory() reverses it. Nothing is hard-deleted.
  - _dense_search() applied the memory_type filter AFTER the vec LIMIT, so a
    type-filtered search could return fewer rows than asked for whenever other
    types ranked higher. It now over-fetches when a type filter is set.
  - list_by_type() for deterministic loading of the house rules.

2026-10-20 (CC): store() and search() both need spaCy and its en_core_web_md model for the entity graph, and
neither was declared, so an install from the [agent] extra failed at the first store, after the row was committed.
spacy is now in the extra, and the store refuses to open without spaCy and the model, naming the install step,
before anything is written.
"""
from __future__ import annotations

import importlib.util
import json
import logging
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import numpy as np

logger = logging.getLogger(__name__)

from . import config

DB_PATH = config.home() / "db" / "apolaki.db"          # $APOLAKI_HOME/db/apolaki.db
WIKI_PATH = config.home() / "wiki"

# Embedding model — loaded lazily
_embedder = None
_nlp = None


def _get_embedder():
    global _embedder
    if _embedder is None:
        from sentence_transformers import SentenceTransformer
        _embedder = SentenceTransformer("all-mpnet-base-v2")
    return _embedder


NLP_MODEL = "en_core_web_md"


def _nlp_missing() -> Optional[str]:
    """What the entity graph is missing, as the step that installs it, or None when spaCy and its model are there."""
    if importlib.util.find_spec("spacy") is None:
        return f'spaCy is not installed: pip install "apolaki[agent]", then python -m spacy download {NLP_MODEL}'
    import spacy.util
    if not spacy.util.is_package(NLP_MODEL):
        return f"spaCy's {NLP_MODEL} model is not installed: python -m spacy download {NLP_MODEL}"
    return None


def _get_nlp():
    global _nlp
    if _nlp is None:
        import spacy
        _nlp = spacy.load(NLP_MODEL, disable=["parser", "lemmatizer"])
        _nlp.add_pipe("sentencizer")
    return _nlp


def _embed(text: str) -> np.ndarray:
    return _get_embedder().encode(text, normalize_embeddings=True)


def _serialize_vec(vec: np.ndarray) -> bytes:
    return vec.astype(np.float32).tobytes()


# ── Schema ──

SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    memory_type TEXT DEFAULT 'observation',
    significance REAL DEFAULT 0.5,
    tags TEXT DEFAULT '[]',
    embedding BLOB,
    created_at TEXT NOT NULL,
    accessed_at TEXT,
    access_count INTEGER DEFAULT 0,
    consolidated INTEGER DEFAULT 0,
    source TEXT DEFAULT '',
    archived INTEGER DEFAULT 0,
    archived_at TEXT,
    archive_reason TEXT
);

CREATE TABLE IF NOT EXISTS kg_entities (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    name_lower TEXT NOT NULL,
    entity_type TEXT DEFAULT 'UNKNOWN',
    embedding BLOB,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    mention_count INTEGER DEFAULT 1,
    UNIQUE(name_lower)
);

CREATE TABLE IF NOT EXISTS kg_triples (
    id TEXT PRIMARY KEY,
    subject_entity_id TEXT NOT NULL REFERENCES kg_entities(id),
    predicate TEXT NOT NULL,
    object_entity_id TEXT NOT NULL REFERENCES kg_entities(id),
    source_memory_id TEXT NOT NULL REFERENCES memories(id),
    confidence REAL DEFAULT 1.0,
    predicate_embedding BLOB,
    created_at TEXT NOT NULL,
    UNIQUE(subject_entity_id, predicate, object_entity_id, source_memory_id)
);

CREATE TABLE IF NOT EXISTS kg_entity_mentions (
    id TEXT PRIMARY KEY,
    entity_id TEXT NOT NULL REFERENCES kg_entities(id),
    memory_id TEXT NOT NULL REFERENCES memories(id),
    mention_context TEXT,
    UNIQUE(entity_id, memory_id)
);

CREATE TABLE IF NOT EXISTS wiki_pages (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL UNIQUE,
    content TEXT NOT NULL,
    entity_ids TEXT DEFAULT '[]',
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_memories_type ON memories(memory_type);
CREATE INDEX IF NOT EXISTS idx_memories_significance ON memories(significance DESC);
CREATE INDEX IF NOT EXISTS idx_kg_entities_name ON kg_entities(name_lower);
CREATE INDEX IF NOT EXISTS idx_kg_triples_subject ON kg_triples(subject_entity_id);
CREATE INDEX IF NOT EXISTS idx_kg_triples_object ON kg_triples(object_entity_id);
CREATE INDEX IF NOT EXISTS idx_kg_mentions_entity ON kg_entity_mentions(entity_id);
CREATE INDEX IF NOT EXISTS idx_kg_mentions_memory ON kg_entity_mentions(memory_id);
"""

# Columns added after the original schema shipped; applied with ALTER TABLE on
# databases that predate them. (CREATE TABLE IF NOT EXISTS does not add columns.)
MIGRATION_COLUMNS = (
    ("archived", "INTEGER DEFAULT 0"),
    ("archived_at", "TEXT"),
    ("archive_reason", "TEXT"),
)

RELEVANT_ENTITY_TYPES = {
    "PERSON", "ORG", "GPE", "EVENT", "PRODUCT",
    "WORK_OF_ART", "FAC", "NORP", "LOC", "LAW"
}

MEMORY_COLUMNS = "id, content, memory_type, significance, tags, created_at, source"


# ── Data Classes ──

@dataclass
class Memory:
    id: str
    content: str
    memory_type: str = "observation"
    significance: float = 0.5
    tags: list[str] = field(default_factory=list)
    embedding: Optional[np.ndarray] = None
    created_at: str = ""
    source: str = ""


@dataclass
class Entity:
    id: str
    name: str
    entity_type: str
    mention_count: int = 1


@dataclass
class Triple:
    subject: str
    predicate: str
    object: str
    confidence: float = 1.0


@dataclass
class SearchResult:
    memory: Memory
    score: float
    source: str = "dense"
    graph_entities: list[str] = field(default_factory=list)
    verification: str = ""


def _row_to_memory(row) -> Memory:
    return Memory(
        id=row[0], content=row[1], memory_type=row[2], significance=row[3],
        tags=json.loads(row[4] or "[]"), created_at=row[5], source=row[6] or "",
    )


# ── Store ──

class ApolakiMemoryStore:
    """Full-stack memory: CMA + KG + TGS + Wiki on SQLite."""

    def __init__(self, db_path: Path = DB_PATH, wiki_path: Path = WIKI_PATH):
        missing = _nlp_missing()
        if missing:     # every store() and search() reaches the entity graph; refuse before writing anything
            raise RuntimeError(f"the memory store can't open: {missing}")
        self.db_path = db_path
        self.wiki_path = wiki_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        wiki_path.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path))
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()
        self._init_vec()

    def _init_schema(self):
        self.conn.executescript(SCHEMA)
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(memories)")}
        for col, decl in MIGRATION_COLUMNS:
            if col not in cols:
                logger.info("migrating memories: adding column %s", col)
                self.conn.execute(f"ALTER TABLE memories ADD COLUMN {col} {decl}")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_archived ON memories(archived)")
        self.conn.commit()

    def _init_vec(self):
        try:
            import sqlite_vec
            self.conn.enable_load_extension(True)
            sqlite_vec.load(self.conn)
            self.conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS memory_vec USING vec0(
                    id TEXT PRIMARY KEY,
                    embedding float[768]
                )
            """)
            self.conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS entity_vec USING vec0(
                    id TEXT PRIMARY KEY,
                    embedding float[768]
                )
            """)
            self.conn.commit()
            self._vec_enabled = True
        except Exception as e:
            logger.warning("sqlite-vec not available, falling back to brute-force: %s", e)
            self._vec_enabled = False

    # ── CMA: Store ──

    def store(
        self,
        content: str,
        memory_type: str = "observation",
        significance: float = 0.5,
        tags: list[str] | None = None,
        source: str = "",
    ) -> str:
        mid = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        embedding = _embed(content)

        self.conn.execute(
            "INSERT INTO memories (id, content, memory_type, significance, tags, embedding, created_at, source) VALUES (?,?,?,?,?,?,?,?)",
            (mid, content, memory_type, significance, json.dumps(tags or []),
             _serialize_vec(embedding), now, source),
        )

        if self._vec_enabled:
            self.conn.execute(
                "INSERT INTO memory_vec (id, embedding) VALUES (?, ?)",
                (mid, _serialize_vec(embedding)),
            )

        self.conn.commit()

        # KG extraction (async-ish, but we're sync here)
        self._extract_kg(mid, content, embedding)

        return mid

    # ── Archive (never delete) ──

    def archive_memory(self, memory_id: str, reason: str = "") -> bool:
        """Hide a memory from every read path. The row and its embedding blob stay;
        only the vec-index entry is removed (rebuilt by unarchive_memory)."""
        row = self.conn.execute("SELECT archived FROM memories WHERE id = ?", (memory_id,)).fetchone()
        if not row:
            return False
        self.conn.execute(
            "UPDATE memories SET archived = 1, archived_at = ?, archive_reason = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), reason, memory_id),
        )
        if self._vec_enabled:
            self.conn.execute("DELETE FROM memory_vec WHERE id = ?", (memory_id,))
        self.conn.commit()
        return True

    def unarchive_memory(self, memory_id: str) -> bool:
        row = self.conn.execute(
            "SELECT embedding FROM memories WHERE id = ? AND archived = 1", (memory_id,)
        ).fetchone()
        if not row:
            return False
        self.conn.execute(
            "UPDATE memories SET archived = 0, archived_at = NULL, archive_reason = NULL WHERE id = ?",
            (memory_id,),
        )
        if self._vec_enabled and row[0]:
            self.conn.execute("DELETE FROM memory_vec WHERE id = ?", (memory_id,))
            self.conn.execute("INSERT INTO memory_vec (id, embedding) VALUES (?, ?)", (memory_id, row[0]))
        self.conn.commit()
        return True

    def find_by_prefix(self, id_prefix: str) -> list[tuple[str, str, int]]:
        """(id, content, archived) for every memory whose id starts with the prefix."""
        return self.conn.execute(
            "SELECT id, content, archived FROM memories WHERE id LIKE ?", (id_prefix + "%",)
        ).fetchall()

    def list_by_type(self, memory_types, include_archived: bool = False) -> list[Memory]:
        """All memories of the given type(s), most significant first, oldest first within a tie."""
        if isinstance(memory_types, str):
            memory_types = (memory_types,)
        types = tuple(memory_types)
        if not types:
            return []
        q = "SELECT %s FROM memories WHERE memory_type IN (%s)" % (MEMORY_COLUMNS, ",".join("?" * len(types)))
        if not include_archived:
            q += " AND archived = 0"
        q += " ORDER BY significance DESC, created_at ASC"
        return [_row_to_memory(r) for r in self.conn.execute(q, types).fetchall()]

    # ── CMA: Search ──

    def search(
        self,
        query: str,
        n_results: int = 10,
        memory_type: str | None = None,
        use_graph: bool = True,
    ) -> list[SearchResult]:
        query_emb = _embed(query)

        # Dense search
        dense_results = self._dense_search(query_emb, n_results * 2, memory_type)

        if not use_graph:
            return dense_results[:n_results]

        # Graph search
        graph_results = self._graph_search(query, query_emb, n_results * 2)

        # TGS verification
        verified = self._tgs_verify(dense_results, graph_results, query_emb)

        return verified[:n_results]

    def _dense_search(
        self, query_emb: np.ndarray, limit: int, memory_type: str | None = None,
    ) -> list[SearchResult]:
        # The type filter is applied to rows AFTER the ANN query, so with a filter
        # set the ANN query must return more candidates than the caller asked for.
        k = limit if memory_type is None else max(limit * 4, 32)
        if self._vec_enabled:
            rows = self.conn.execute(
                "SELECT id, distance FROM memory_vec WHERE embedding MATCH ? ORDER BY distance LIMIT ?",
                (_serialize_vec(query_emb), k),
            ).fetchall()
            ids_scores = {row[0]: 1.0 - row[1] for row in rows}
        else:
            # Brute-force cosine similarity
            all_rows = self.conn.execute(
                "SELECT id, embedding FROM memories WHERE embedding IS NOT NULL AND archived = 0"
            ).fetchall()
            ids_scores = {}
            for row in all_rows:
                emb = np.frombuffer(row[1], dtype=np.float32)
                sim = float(np.dot(query_emb, emb))
                ids_scores[row[0]] = sim

        results = []
        for mid, score in sorted(ids_scores.items(), key=lambda x: -x[1]):
            if len(results) >= limit:
                break
            row = self.conn.execute(
                "SELECT %s FROM memories WHERE id = ? AND archived = 0" % MEMORY_COLUMNS,
                (mid,),
            ).fetchone()
            if row and (memory_type is None or row[2] == memory_type):
                results.append(SearchResult(memory=_row_to_memory(row), score=score, source="dense"))

                # Bump access count
                self.conn.execute(
                    "UPDATE memories SET access_count = access_count + 1, accessed_at = ? WHERE id = ?",
                    (datetime.now(timezone.utc).isoformat(), mid),
                )

        self.conn.commit()
        return results

    # ── KG: Extract ──

    def _extract_kg(self, memory_id: str, content: str, content_embedding: np.ndarray):
        nlp = _get_nlp()
        doc = nlp(content)

        entities = []
        seen = set()
        for ent in doc.ents:
            if ent.label_ not in RELEVANT_ENTITY_TYPES:
                continue
            name = ent.text.strip()
            if len(name) < 2 or name.lower() in seen:
                continue
            seen.add(name.lower())
            entities.append({
                "name": name,
                "type": ent.label_,
                "start": ent.start_char,
                "context": ent.sent.text.strip() if ent.sent else content[max(0, ent.start_char-50):ent.end_char+50],
            })

        entity_ids = {}
        for ent in entities:
            eid = self._upsert_entity(ent["name"], ent["type"])
            entity_ids[ent["name"]] = eid
            try:
                self.conn.execute(
                    "INSERT OR IGNORE INTO kg_entity_mentions (id, entity_id, memory_id, mention_context) VALUES (?,?,?,?)",
                    (str(uuid.uuid4()), eid, memory_id, ent["context"]),
                )
            except sqlite3.IntegrityError:
                pass

        # Co-occurrence triples
        from itertools import combinations
        for e1, e2 in combinations(entities, 2):
            dist = abs(e1["start"] - e2["start"])
            if dist <= 200:
                confidence = round(1.0 - 0.5 * (dist / 200), 3)
                self._insert_triple(
                    entity_ids[e1["name"]], "co_occurs_with",
                    entity_ids[e2["name"]], memory_id, confidence,
                )

        self.conn.commit()

    def _upsert_entity(self, name: str, entity_type: str) -> str:
        row = self.conn.execute(
            "SELECT id, mention_count FROM kg_entities WHERE name_lower = ?",
            (name.lower(),),
        ).fetchone()

        if row:
            self.conn.execute(
                "UPDATE kg_entities SET mention_count = ?, last_seen = ?, entity_type = ? WHERE id = ?",
                (row[1] + 1, datetime.now(timezone.utc).isoformat(), entity_type, row[0]),
            )
            return row[0]

        eid = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        emb = _embed(name)
        self.conn.execute(
            "INSERT INTO kg_entities (id, name, name_lower, entity_type, embedding, first_seen, last_seen) VALUES (?,?,?,?,?,?,?)",
            (eid, name, name.lower(), entity_type, _serialize_vec(emb), now, now),
        )
        if self._vec_enabled:
            self.conn.execute(
                "INSERT INTO entity_vec (id, embedding) VALUES (?, ?)",
                (eid, _serialize_vec(emb)),
            )
        return eid

    def _insert_triple(self, subj_id: str, predicate: str, obj_id: str, memory_id: str, confidence: float):
        try:
            self.conn.execute(
                "INSERT OR IGNORE INTO kg_triples (id, subject_entity_id, predicate, object_entity_id, source_memory_id, confidence, created_at) VALUES (?,?,?,?,?,?,?)",
                (str(uuid.uuid4()), subj_id, predicate, obj_id, memory_id, confidence,
                 datetime.now(timezone.utc).isoformat()),
            )
        except sqlite3.IntegrityError:
            pass

    # ── KG: Graph Search ──

    def _graph_search(self, query: str, query_emb: np.ndarray, limit: int) -> list[SearchResult]:
        nlp = _get_nlp()
        doc = nlp(query)
        query_entities = [ent.text.strip() for ent in doc.ents if ent.label_ in RELEVANT_ENTITY_TYPES]

        if not query_entities:
            return []

        # Find seed nodes
        seeds = {}
        for name in query_entities:
            row = self.conn.execute(
                "SELECT id FROM kg_entities WHERE name_lower = ?", (name.lower(),)
            ).fetchone()
            if row:
                seeds[row[0]] = 1.0
            elif self._vec_enabled:
                name_emb = _embed(name)
                rows = self.conn.execute(
                    "SELECT id, distance FROM entity_vec WHERE embedding MATCH ? ORDER BY distance LIMIT 3",
                    (_serialize_vec(name_emb),),
                ).fetchall()
                for r in rows:
                    if 1.0 - r[1] >= 0.85:
                        seeds[r[0]] = 1.0 - r[1]

        if not seeds:
            return []

        # Load adjacency and run PPR
        entities_rows = self.conn.execute("SELECT id FROM kg_entities ORDER BY id").fetchall()
        entity_ids = [r[0] for r in entities_rows]
        if not entity_ids:
            return []

        node_idx = {eid: i for i, eid in enumerate(entity_ids)}
        n = len(entity_ids)
        adj = np.zeros((n, n), dtype=np.float64)

        triples = self.conn.execute(
            "SELECT subject_entity_id, object_entity_id, confidence FROM kg_triples"
        ).fetchall()

        for subj, obj, conf in triples:
            si, oi = node_idx.get(subj), node_idx.get(obj)
            if si is not None and oi is not None:
                adj[si, oi] = conf
                adj[oi, si] = conf

        # PPR
        seed_indices = {node_idx[eid]: w for eid, w in seeds.items() if eid in node_idx}
        ppr = self._personalized_pagerank(adj, seed_indices)

        # Map to memories
        idx_to_eid = {i: eid for eid, i in node_idx.items()}
        top_indices = np.argsort(ppr)[::-1][:20]

        top_eids = [(idx_to_eid[i], ppr[i]) for i in top_indices if ppr[i] > 1e-8]

        memory_scores: dict[str, float] = {}
        memory_entities: dict[str, list[str]] = {}

        for eid, score in top_eids:
            # Archived memories keep their mention rows but never come back here.
            mentions = self.conn.execute(
                "SELECT em.memory_id FROM kg_entity_mentions em "
                "JOIN memories m ON m.id = em.memory_id "
                "WHERE em.entity_id = ? AND m.archived = 0", (eid,)
            ).fetchall()
            ename = self.conn.execute("SELECT name FROM kg_entities WHERE id=?", (eid,)).fetchone()
            for (mid,) in mentions:
                memory_scores[mid] = memory_scores.get(mid, 0) + score
                memory_entities.setdefault(mid, []).append(ename[0] if ename else eid)

        results = []
        for mid, score in sorted(memory_scores.items(), key=lambda x: -x[1])[:limit]:
            row = self.conn.execute(
                "SELECT %s FROM memories WHERE id = ? AND archived = 0" % MEMORY_COLUMNS,
                (mid,),
            ).fetchone()
            if row:
                results.append(SearchResult(
                    memory=_row_to_memory(row), score=score, source="graph",
                    graph_entities=memory_entities.get(mid, []),
                ))

        return results

    def _personalized_pagerank(
        self, adj: np.ndarray, seeds: dict[int, float],
        alpha: float = 0.15, max_iter: int = 50, tol: float = 1e-6,
    ) -> np.ndarray:
        n = adj.shape[0]
        if n == 0:
            return np.array([])

        col_sums = adj.sum(axis=0)
        col_sums[col_sums == 0] = 1.0
        T = adj / col_sums

        p = np.zeros(n)
        total_w = sum(seeds.values())
        if total_w > 0:
            for idx, w in seeds.items():
                p[idx] = w / total_w
        else:
            p[:] = 1.0 / n

        scores = p.copy()
        for _ in range(max_iter):
            prev = scores.copy()
            scores = (1 - alpha) * (T @ scores) + alpha * p
            if np.abs(scores - prev).sum() < tol:
                break

        return scores

    # ── TGS Verification ──

    def _tgs_verify(
        self,
        dense_results: list[SearchResult],
        graph_results: list[SearchResult],
        query_emb: np.ndarray,
        graph_weight: float = 0.4,
        entity_boost: float = 0.1,
    ) -> list[SearchResult]:
        graph_entities = set()
        for gr in graph_results:
            graph_entities.update(gr.graph_entities)

        all_results: dict[str, SearchResult] = {}
        entity_lower = {e.lower() for e in graph_entities}

        # Score dense results with graph voting
        for dr in dense_results:
            content_lower = dr.memory.content.lower()
            overlap = [e for e in graph_entities if e.lower() in content_lower]
            coverage = len(overlap) / max(len(entity_lower), 1)
            graph_score = coverage + len(overlap) * entity_boost

            combined = dr.score * (1 - graph_weight) + graph_score * graph_weight

            if len(overlap) >= 2:
                verification = "confirmed"
            elif len(overlap) == 1:
                verification = "partial"
            elif dr.score > 0.7:
                verification = "text_only"
            else:
                verification = "weakened"

            all_results[dr.memory.id] = SearchResult(
                memory=dr.memory, score=combined, source="tgs",
                graph_entities=overlap, verification=verification,
            )

        # Add graph-only results
        for gr in graph_results:
            if gr.memory.id not in all_results:
                all_results[gr.memory.id] = SearchResult(
                    memory=gr.memory, score=gr.score * graph_weight,
                    source="graph", graph_entities=gr.graph_entities,
                    verification="graph_only",
                )

        return sorted(all_results.values(), key=lambda x: -x.score)

    # ── Wiki Generation ──

    def generate_wiki_page(self, entity_name: str) -> str | None:
        row = self.conn.execute(
            "SELECT id, entity_type, mention_count FROM kg_entities WHERE name_lower = ?",
            (entity_name.lower(),),
        ).fetchone()
        if not row:
            return None

        eid, etype, mentions = row

        # Get all memories mentioning this entity
        mem_rows = self.conn.execute("""
            SELECT m.content, m.memory_type, m.created_at, em.mention_context
            FROM kg_entity_mentions em
            JOIN memories m ON m.id = em.memory_id
            WHERE em.entity_id = ? AND m.archived = 0
            ORDER BY m.created_at DESC
        """, (eid,)).fetchall()

        # Get connected entities via triples
        triple_rows = self.conn.execute("""
            SELECT e2.name, t.predicate, t.confidence
            FROM kg_triples t
            JOIN kg_entities e2 ON e2.id = t.object_entity_id
            WHERE t.subject_entity_id = ?
            UNION
            SELECT e2.name, t.predicate, t.confidence
            FROM kg_triples t
            JOIN kg_entities e2 ON e2.id = t.subject_entity_id
            WHERE t.object_entity_id = ?
        """, (eid, eid)).fetchall()

        # Build wiki page
        lines = [f"# {entity_name}", f"**Type:** {etype} | **Mentions:** {mentions}", ""]

        if triple_rows:
            lines.append("## Connected Entities")
            seen = set()
            for name, pred, conf in sorted(triple_rows, key=lambda x: -x[2]):
                if name not in seen:
                    lines.append(f"- [[{name}]] ({pred}, confidence: {conf:.2f})")
                    seen.add(name)
            lines.append("")

        if mem_rows:
            lines.append("## Memory References")
            for content, mtype, created, context in mem_rows[:10]:
                lines.append(f"- [{mtype}] {context or content[:120]}... ({created[:10]})")
            lines.append("")

        page_content = "\n".join(lines)

        # Save to wiki dir
        safe_name = entity_name.lower().replace(" ", "_").replace("/", "_")
        wiki_file = self.wiki_path / f"{safe_name}.md"
        wiki_file.write_text(page_content)

        # Update wiki_pages table
        self.conn.execute(
            "INSERT OR REPLACE INTO wiki_pages (id, title, content, entity_ids, updated_at) VALUES (?,?,?,?,?)",
            (str(uuid.uuid4()), entity_name, page_content,
             json.dumps([eid]), datetime.now(timezone.utc).isoformat()),
        )
        self.conn.commit()

        return page_content

    # ── Stats ──

    def stats(self) -> dict:
        memories = self.conn.execute("SELECT COUNT(*) FROM memories WHERE archived = 0").fetchone()[0]
        archived = self.conn.execute("SELECT COUNT(*) FROM memories WHERE archived = 1").fetchone()[0]
        entities = self.conn.execute("SELECT COUNT(*) FROM kg_entities").fetchone()[0]
        triples = self.conn.execute("SELECT COUNT(*) FROM kg_triples").fetchone()[0]
        wiki_pages = self.conn.execute("SELECT COUNT(*) FROM wiki_pages").fetchone()[0]

        top_entities = self.conn.execute(
            "SELECT name, entity_type, mention_count FROM kg_entities ORDER BY mention_count DESC LIMIT 10"
        ).fetchall()

        return {
            "memories": memories,
            "archived": archived,
            "entities": entities,
            "triples": triples,
            "wiki_pages": wiki_pages,
            "top_entities": [
                {"name": r[0], "type": r[1], "mentions": r[2]} for r in top_entities
            ],
        }

    def close(self):
        self.conn.close()
