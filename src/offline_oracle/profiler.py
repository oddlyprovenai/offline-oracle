# generated-by: gemini-ultra
from __future__ import annotations

import platform
import resource
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

from offline_oracle.indexer import OracleIndex
from offline_oracle.synthesizer import OracleSynthesizer
from offline_oracle.zim_reader import ZimArchive, build_mini_zim, iter_jsonl_articles


def get_process_rss_mib() -> float:
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform == "darwin":
        return round(usage / (1024.0 * 1024.0), 2)
    return round(usage / 1024.0, 2)


def _percentile(sorted_vals: list[float], pct: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = min(len(sorted_vals) - 1, int((pct / 100.0) * len(sorted_vals)))
    return round(sorted_vals[idx], 3)


def run_benchmark(
    work_dir: str | Path | None = None,
    fixture_jsonl: str | Path | None = None,
) -> dict:
    default_fixture = (
        Path(__file__).resolve().parent.parent.parent / "fixtures" / "synthetic_encyclopedia.jsonl"
    )
    src_jsonl = Path(fixture_jsonl) if fixture_jsonl is not None else default_fixture

    temp_ctx = tempfile.TemporaryDirectory() if work_dir is None else None
    try:
        base_dir = Path(temp_ctx.name) if temp_ctx is not None else Path(work_dir)
        zim_path = base_dir / "bench_archive.zim"
        db_path = base_dir / "bench_index.sqlite"

        articles = list(iter_jsonl_articles(src_jsonl))
        zim_specs = [
            {
                "url": f"{a.slug}.html",
                "title": a.title,
                "html": "<html><body>" + "".join(f"<p>{p}</p>" for p in a.paragraphs) + "</body></html>",
                "namespace": "C",
            }
            for a in articles
        ]

        tracemalloc.start()
        t_build = time.perf_counter()
        build_mini_zim(zim_path, zim_specs, compression=4)

        with ZimArchive(zim_path) as zf:
            magic_ok = zf.header.magic_number

        t_index_start = time.perf_counter()
        with OracleIndex(db_path) as idx:
            ingest_stats = idx.ingest_zim(zim_path, batch_size=4)
            index_ms = round((time.perf_counter() - t_index_start) * 1000.0, 3)

            synth = OracleSynthesizer(idx)
            queries = [
                "What magic integer starts an OpenZIM binary header?",
                "Why do wristwatches vibrate a quartz crystal at 32768 hertz?",
                "piezoeletric quarts oscilator vibrat",
                "How were binary ones and zeros woven into Apollo core rope memory?",
                "What temperature are the Svalbard seed chambers cooled to?",
                "How do submarine optical cables boost 1550nm laser signals?",
                "Why do binary cycle geothermal plants use isobutane in a closed loop?",
                "How does SimHash differ from cryptographic hashes on spelling typos?",
            ]

            latencies: list[float] = []
            verified_total = 0
            claims_total = 0
            for q in queries:
                res = synth.ask(q)
                latencies.append(float(res["latency_ms"]))
                verified_total += int(res["verification"]["verified_claims"])
                claims_total += int(res["verification"]["total_claims"])

            _, peak_heap_bytes = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            idx.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

            db_stats = idx.get_stats()

        latencies.sort()
        ver_rate = round(verified_total / claims_total, 4) if claims_total > 0 else 0.0

        return {
            "python_version": platform.python_version(),
            "zim_magic_verified": magic_ok,
            "zim_bytes": zim_path.stat().st_size,
            "articles_indexed": ingest_stats["articles_indexed"],
            "paragraphs_indexed": ingest_stats["paragraphs_indexed"],
            "index_time_ms": index_ms,
            "index_disk_bytes": db_stats["disk_bytes"],
            "peak_heap_kib": round(peak_heap_bytes / 1024.0, 2),
            "peak_heap_mib": round(peak_heap_bytes / (1024.0 * 1024.0), 3),
            "process_rss_mib": get_process_rss_mib(),
            "queries_executed": len(queries),
            "query_p50_ms": _percentile(latencies, 50),
            "query_p95_ms": _percentile(latencies, 95),
            "claims_checked": claims_total,
            "claims_verified": verified_total,
            "citation_verification_rate": ver_rate,
            "total_elapsed_ms": round((time.perf_counter() - t_build) * 1000.0, 2),
        }
    finally:
        if temp_ctx is not None:
            temp_ctx.cleanup()
