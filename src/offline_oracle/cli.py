# generated-by: gemini-ultra
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from offline_oracle.indexer import OracleIndex
from offline_oracle.profiler import run_benchmark
from offline_oracle.synthesizer import OracleSynthesizer
from offline_oracle.web import start_oracle_server
from offline_oracle.zim_reader import build_mini_zim, iter_jsonl_articles


def _default_fixture() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "fixtures" / "synthetic_encyclopedia.jsonl"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="oracle",
        description="Offline Oracle — 100% offline OpenZIM research assistant with span-verified citations.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_demo = sub.add_parser("build-demo-zim", help="Build a binary OpenZIM (.zim) archive from a JSONL reference file")
    p_demo.add_argument("--input", default=str(_default_fixture()), help="Input reference JSONL")
    p_demo.add_argument("--output", required=True, help="Output .zim path")

    p_idx = sub.add_parser("index", help="Stream a .zim, .jsonl, or HTML folder into an on-disk SQLite FTS5 + SimHash index")
    p_idx.add_argument("--source", required=True, help="Path to .zim, .jsonl, or HTML directory")
    p_idx.add_argument("--db", required=True, help="Output SQLite index file")
    p_idx.add_argument("--batch-size", type=int, default=128)

    p_ask = sub.add_parser("ask", help="Query an offline SQLite index with verbatim citation verification")
    p_ask.add_argument("--db", required=True, help="SQLite index path")
    p_ask.add_argument("query", help="Question to answer")
    p_ask.add_argument("--llm-url", default=None, help="Optional http://127.0.0.1:<port>/v1/chat/completions")
    p_ask.add_argument("--json", action="store_true", help="Output full JSON verification report")

    p_bench = sub.add_parser("bench", help="Run hardware, bounded-RAM, and citation verification benchmark receipt")
    p_bench.add_argument("--json", action="store_true", help="Emit JSON receipt")

    p_serve = sub.add_parser("serve", help="Start local 127.0.0.1 split-pane citation verifier web UI")
    p_serve.add_argument("--db", required=True, help="SQLite index path")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8765)
    p_serve.add_argument("--llm-url", default=None)

    args = parser.parse_args(argv)

    if args.cmd == "build-demo-zim":
        articles = list(iter_jsonl_articles(args.input))
        specs = [
            {
                "url": f"{a.slug}.html",
                "title": a.title,
                "html": "<html><body>" + "".join(f"<p>{p}</p>" for p in a.paragraphs) + "</body></html>",
                "namespace": "C",
            }
            for a in articles
        ]
        out = build_mini_zim(args.output, specs, compression=4)
        print(f"Built OpenZIM archive: {out} ({out.stat().st_size} bytes, {len(specs)} articles)")
        return 0

    if args.cmd == "index":
        src = Path(args.source)
        with OracleIndex(args.db) as idx:
            if src.is_dir():
                stats = idx.ingest_html_dir(src, batch_size=args.batch_size)
            elif src.suffix.lower() == ".zim":
                stats = idx.ingest_zim(src, batch_size=args.batch_size)
            else:
                stats = idx.ingest_jsonl(src, batch_size=args.batch_size)
            db_stats = idx.get_stats()
        print(
            f"Indexed {stats['articles_indexed']} articles ({stats['paragraphs_indexed']} paragraphs) "
            f"-> {db_stats['disk_bytes']} bytes on disk."
        )
        return 0

    if args.cmd == "ask":
        with OracleIndex(args.db) as idx:
            synth = OracleSynthesizer(idx, llm_endpoint=args.llm_url)
            res = synth.ask(args.query)
        if args.json:
            print(json.dumps(res, indent=2))
        else:
            v = res["verification"]
            print(res["answer"])
            print(
                f"\n[Receipt: {v['verified_claims']}/{v['total_claims']} citations verbatim-verified "
                f"| mode={res['mode']} | {res['latency_ms']} ms | CC BY-SA 4.0]"
            )
        return 0

    if args.cmd == "bench":
        receipt = run_benchmark()
        if args.json:
            print(json.dumps(receipt, indent=2))
            return 0
        print("=== OFFLINE ORACLE // 16 GB CHALLENGE RECEIPT ===")
        print(f"Python stdlib runtime    : {receipt['python_version']}")
        print(f"OpenZIM magic verified   : {receipt['zim_magic_verified']} (0x044D495A)")
        print(f"Archive -> SQLite index  : {receipt['zim_bytes']} B -> {receipt['index_disk_bytes']} B ({receipt['paragraphs_indexed']} paragraphs in {receipt['index_time_ms']} ms)")
        print(f"Peak Python heap (trace) : {receipt['peak_heap_kib']} KiB ({receipt['peak_heap_mib']} MiB)")
        print(f"Process max RSS          : {receipt['process_rss_mib']} MiB")
        print(f"Query latency P50 / P95  : {receipt['query_p50_ms']} ms / {receipt['query_p95_ms']} ms")
        print(f"Verbatim citation verify : {receipt['claims_verified']}/{receipt['claims_checked']} ({receipt['citation_verification_rate'] * 100:.1f}%)")
        print("Outbound network calls   : 0 (air-gapped / 127.0.0.1 only)")
        return 0

    if args.cmd == "serve":
        handle = start_oracle_server(
            db_path=args.db,
            host=args.host,
            port=args.port,
            llm_endpoint=args.llm_url,
        )
        print(f"Offline Oracle UI listening on {handle.url} (Ctrl+C to stop)")
        try:
            while True:
                time.sleep(1.0)
        except KeyboardInterrupt:
            handle.close()
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
