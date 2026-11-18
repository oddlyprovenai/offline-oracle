# generated-by: gemini-ultra
from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from offline_oracle.zim_reader import (
    ArticleRecord,
    ZimArchive,
    iter_html_directory,
    iter_jsonl_articles,
)

STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "that",
    "this",
    "from",
    "into",
    "what",
    "when",
    "where",
    "which",
    "how",
    "were",
    "was",
    "are",
    "does",
    "have",
    "has",
}


@dataclass(frozen=True)
class SearchHit:
    chunk_id: int
    slug: str
    title: str
    paragraph_index: int
    text: str
    license: str
    bm25_score: float
    trigram_score: float
    simhash_score: float
    hybrid_score: float

    @property
    def anchor(self) -> str:
        return f"{self.slug}#p{self.paragraph_index}"


def _normalize_for_grams(text: str) -> str:
    lowered = re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()
    return f"  {lowered}  " if lowered else ""


def extract_char_trigrams(text: str) -> set[str]:
    norm = _normalize_for_grams(text)
    if len(norm) < 3:
        return set()
    return {norm[i : i + 3] for i in range(len(norm) - 2)}


def char_trigram_jaccard(text_a: str, text_b: str) -> float:
    grams_a = extract_char_trigrams(text_a)
    grams_b = extract_char_trigrams(text_b)
    if not grams_a or not grams_b:
        return 0.0
    inter = len(grams_a & grams_b)
    union = len(grams_a | grams_b)
    return inter / union if union else 0.0


def compute_simhash64(text: str) -> int:
    grams = extract_char_trigrams(text)
    if not grams:
        return 0
    weights = [0] * 64
    for gram in grams:
        digest = hashlib.blake2b(gram.encode("utf-8"), digest_size=8).digest()
        val = int.from_bytes(digest, "little")
        for bit in range(64):
            if (val >> bit) & 1:
                weights[bit] += 1
            else:
                weights[bit] -= 1
    out = 0
    for bit in range(64):
        if weights[bit] > 0:
            out |= 1 << bit
    return out


def hamming_distance64(a: int, b: int) -> int:
    return ((a ^ b) & 0xFFFFFFFFFFFFFFFF).bit_count()


def _u64_to_i64(val: int) -> int:
    val &= 0xFFFFFFFFFFFFFFFF
    return val - (1 << 64) if val >= (1 << 63) else val


def _i64_to_u64(val: int) -> int:
    return val & 0xFFFFFFFFFFFFFFFF


class OracleIndex:
    """Bounded-RAM SQLite FTS5 + 64-bit SimHash + Char 3-Gram Hybrid Store."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "OracleIndex":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _init_schema(self) -> None:
        cur = self.conn.cursor()
        cur.executescript(
            """
            PRAGMA journal_mode = WAL;
            PRAGMA synchronous = NORMAL;

            CREATE TABLE IF NOT EXISTS articles (
                slug TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                url TEXT NOT NULL,
                license TEXT NOT NULL,
                paragraph_count INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slug TEXT NOT NULL,
                title TEXT NOT NULL,
                paragraph_index INTEGER NOT NULL,
                text TEXT NOT NULL,
                license TEXT NOT NULL,
                simhash64 INTEGER NOT NULL,
                UNIQUE(slug, paragraph_index)
            );

            CREATE INDEX IF NOT EXISTS idx_chunks_slug_para ON chunks(slug, paragraph_index);

            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                title,
                text,
                content='chunks',
                content_rowid='id',
                prefix='4',
                tokenize='unicode61'
            );
            """
        )
        self.conn.commit()

    def ingest_stream(self, articles: Iterable[ArticleRecord], batch_size: int = 128) -> dict[str, int]:
        articles_count = 0
        paragraphs_count = 0
        chunk_batch: list[tuple] = []
        article_batch: list[tuple] = []

        for art in articles:
            articles_count += 1
            article_batch.append(
                (art.slug, art.title, art.url, art.license, len(art.paragraphs))
            )
            for p_idx, para in enumerate(art.paragraphs):
                paragraphs_count += 1
                sh64 = _u64_to_i64(compute_simhash64(f"{art.title} {para}"))
                chunk_batch.append((art.slug, art.title, p_idx, para, art.license, sh64))

            if len(chunk_batch) >= batch_size:
                self._flush_batches(article_batch, chunk_batch)
                article_batch.clear()
                chunk_batch.clear()

        if article_batch or chunk_batch:
            self._flush_batches(article_batch, chunk_batch)

        return {
            "articles_indexed": articles_count,
            "paragraphs_indexed": paragraphs_count,
        }

    def _flush_batches(self, article_batch: list[tuple], chunk_batch: list[tuple]) -> None:
        with self.conn:
            self.conn.executemany(
                """
                INSERT INTO articles(slug, title, url, license, paragraph_count)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(slug) DO UPDATE SET
                    title = excluded.title,
                    url = excluded.url,
                    license = excluded.license,
                    paragraph_count = excluded.paragraph_count
                """,
                article_batch,
            )
            for slug, title, p_idx, text, lic, sh64 in chunk_batch:
                existing = self.conn.execute(
                    "SELECT id, title, text FROM chunks WHERE slug = ? AND paragraph_index = ?",
                    (slug, p_idx),
                ).fetchone()
                if existing is not None:
                    self.conn.execute(
                        "INSERT INTO chunks_fts(chunks_fts, rowid, title, text) VALUES('delete', ?, ?, ?)",
                        (existing["id"], existing["title"], existing["text"]),
                    )
                    self.conn.execute(
                        "UPDATE chunks SET title=?, text=?, license=?, simhash64=? WHERE id=?",
                        (title, text, lic, sh64, existing["id"]),
                    )
                    row_id = existing["id"]
                else:
                    cur = self.conn.execute(
                        """
                        INSERT INTO chunks(slug, title, paragraph_index, text, license, simhash64)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (slug, title, p_idx, text, lic, sh64),
                    )
                    row_id = cur.lastrowid
                self.conn.execute(
                    "INSERT INTO chunks_fts(rowid, title, text) VALUES (?, ?, ?)",
                    (row_id, title, text),
                )

    def ingest_zim(self, zim_path: str | Path, batch_size: int = 128) -> dict[str, int]:
        with ZimArchive(zim_path) as archive:
            return self.ingest_stream(archive.iter_articles(), batch_size=batch_size)

    def ingest_jsonl(self, jsonl_path: str | Path, batch_size: int = 128) -> dict[str, int]:
        return self.ingest_stream(iter_jsonl_articles(jsonl_path), batch_size=batch_size)

    def ingest_html_dir(self, dir_path: str | Path, batch_size: int = 128) -> dict[str, int]:
        return self.ingest_stream(iter_html_directory(dir_path), batch_size=batch_size)

    def has_article(self, slug: str) -> bool:
        row = self.conn.execute("SELECT 1 FROM articles WHERE slug = ?", (slug,)).fetchone()
        return row is not None

    def get_paragraph(self, slug: str, paragraph_index: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT id, slug, title, paragraph_index, text, license FROM chunks WHERE slug = ? AND paragraph_index = ?",
            (slug, paragraph_index),
        ).fetchone()

    def get_stats(self) -> dict[str, int]:
        self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        art_row = self.conn.execute("SELECT COUNT(*) AS n FROM articles").fetchone()
        chk_row = self.conn.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()
        disk_bytes = self.db_path.stat().st_size if self.db_path.exists() else 0
        return {
            "articles": int(art_row["n"]),
            "paragraphs": int(chk_row["n"]),
            "disk_bytes": disk_bytes,
        }

    def _build_fts_query(self, query: str) -> str:
        tokens = [
            t
            for t in re.findall(r"[a-zA-Z0-9]+", query.lower())
            if len(t) >= 2 and t not in STOPWORDS
        ]
        if not tokens:
            return ""
        clauses: list[str] = []
        for tok in tokens[:10]:
            clauses.append(f'"{tok}"')
            if len(tok) >= 4:
                clauses.append(f"{tok[:4]}*")
        return " OR ".join(dict.fromkeys(clauses))

    def search(self, query: str, top_k: int = 5, candidate_pool: int = 48) -> list[SearchHit]:
        fts_expr = self._build_fts_query(query)
        candidates: dict[int, tuple[sqlite3.Row, float]] = {}

        if fts_expr:
            rows = self.conn.execute(
                """
                SELECT c.id, c.slug, c.title, c.paragraph_index, c.text, c.license, c.simhash64,
                       -bm25(chunks_fts, 2.5, 1.0) AS raw_bm25
                FROM chunks_fts
                JOIN chunks c ON c.id = chunks_fts.rowid
                WHERE chunks_fts MATCH ?
                ORDER BY bm25(chunks_fts, 2.5, 1.0) ASC
                LIMIT ?
                """,
                (fts_expr, candidate_pool),
            ).fetchall()
            for r in rows:
                candidates[int(r["id"])] = (r, max(0.0, float(r["raw_bm25"])))

        # Fuzzy SimHash / Trigram fallback scan if FTS5 returned sparse candidates (e.g., severe initial-letter typos)
        if len(candidates) < min(12, candidate_pool):
            fallback_rows = self.conn.execute(
                "SELECT id, slug, title, paragraph_index, text, license, simhash64 FROM chunks LIMIT 2000"
            ).fetchall()
            for r in fallback_rows:
                cid = int(r["id"])
                if cid not in candidates:
                    candidates[cid] = (r, 0.0)

        if not candidates:
            return []

        max_bm25 = max((bm for _, bm in candidates.values()), default=0.0)
        q_simhash = compute_simhash64(query)
        q_grams = extract_char_trigrams(query)

        scored_hits: list[SearchHit] = []
        for row, raw_bm25 in candidates.values():
            norm_bm25 = (raw_bm25 / max_bm25) if max_bm25 > 1e-9 else 0.0
            combined_doc = f"{row['title']} {row['text']}"
            doc_grams = extract_char_trigrams(combined_doc)
            if q_grams and doc_grams:
                # Query-coverage + Jaccard blend so longer paragraphs aren't overly penalized
                overlap = len(q_grams & doc_grams)
                jaccard = overlap / len(q_grams | doc_grams)
                q_cov = overlap / len(q_grams)
                trigram_sim = 0.5 * jaccard + 0.5 * q_cov
            else:
                trigram_sim = 0.0

            doc_u64 = _i64_to_u64(int(row["simhash64"]))
            simhash_sim = 1.0 - (hamming_distance64(q_simhash, doc_u64) / 64.0)

            hybrid = 0.50 * norm_bm25 + 0.38 * trigram_sim + 0.12 * simhash_sim
            scored_hits.append(
                SearchHit(
                    chunk_id=int(row["id"]),
                    slug=str(row["slug"]),
                    title=str(row["title"]),
                    paragraph_index=int(row["paragraph_index"]),
                    text=str(row["text"]),
                    license=str(row["license"]),
                    bm25_score=round(norm_bm25, 4),
                    trigram_score=round(trigram_sim, 4),
                    simhash_score=round(simhash_sim, 4),
                    hybrid_score=round(hybrid, 4),
                )
            )

        scored_hits.sort(key=lambda h: h.hybrid_score, reverse=True)
        return scored_hits[:top_k]
