# generated-by: gemini-ultra
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

import pytest

from offline_oracle.zim_reader import (
    ZIM_MAGIC,
    ZimArchive,
    ZimFormatError,
    build_mini_zim,
    extract_html_paragraphs,
    iter_html_directory,
    iter_jsonl_articles,
)
from offline_oracle.indexer import (
    OracleIndex,
    char_trigram_jaccard,
    compute_simhash64,
    hamming_distance64,
)
from offline_oracle.verifier import CitationVerifier
from offline_oracle.synthesizer import (
    OracleSynthesizer,
    validate_local_llm_url,
)
from offline_oracle.profiler import run_benchmark
from offline_oracle.web import start_oracle_server

FIXTURE_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "synthetic_encyclopedia.jsonl"


def _sample_zim_specs() -> list[dict]:
    articles = list(iter_jsonl_articles(FIXTURE_PATH))
    specs = []
    for art in articles:
        html_body = "".join(f"<p>{p}</p>" for p in art.paragraphs)
        specs.append(
            {
                "url": f"{art.slug}.html",
                "title": art.title,
                "html": f"<html><body><h1>{art.title}</h1>{html_body}</body></html>",
                "namespace": "C",
            }
        )
    return specs


def test_01_openzim_magic_constant_matches_spec():
    assert ZIM_MAGIC == 72173914
    assert ZIM_MAGIC == 0x044D495A


@pytest.mark.parametrize(
    "compression_name,compression_code",
    [
        ("none", 1),
        ("zlib", 2),
        ("lzma", 4),
        ("zstd", 5),
    ],
)
def test_02_to_05_binary_zim_roundtrip_all_compressions(
    tmp_path: Path, compression_name: str, compression_code: int
):
    zim_path = tmp_path / f"sample_{compression_name}.zim"
    specs = _sample_zim_specs()
    build_mini_zim(zim_path, specs, compression=compression_code)

    with ZimArchive(zim_path) as archive:
        assert archive.header.magic_number == 72173914
        assert archive.header.entry_count == len(specs)
        extracted = list(archive.iter_articles())
        assert len(extracted) == len(specs)
        slugs = {a.slug for a in extracted}
        assert "quartz_piezoelectric_oscillator" in slugs
        assert "svalbard_seed_vault" in slugs


def test_06_extended_64bit_cluster_offsets_supported(tmp_path: Path):
    zim_path = tmp_path / "extended_offsets.zim"
    specs = _sample_zim_specs()[:3]
    build_mini_zim(zim_path, specs, compression=4, extended_cluster=True)

    with ZimArchive(zim_path) as archive:
        items = list(archive.iter_articles())
        assert len(items) == 3
        assert any("72173914" in p for a in items for p in a.paragraphs)


def test_07_redirect_entry_0xffff_resolves_cleanly(tmp_path: Path):
    zim_path = tmp_path / "redirects.zim"
    specs = _sample_zim_specs()[:2]
    redirects = [{"url": "zim_alias.html", "title": "ZIM Alias", "target_index": 0}]
    build_mini_zim(zim_path, specs, compression=2, redirects=redirects)

    with ZimArchive(zim_path) as archive:
        resolved = archive.read_entry_by_url("zim_alias.html")
        assert resolved is not None
        assert "72173914" in " ".join(resolved.paragraphs)


def test_08_corrupt_magic_raises_zim_format_error(tmp_path: Path):
    bad_file = tmp_path / "corrupt.zim"
    bad_file.write_bytes(b"NOT_A_ZIM_HEADER" * 8)
    with pytest.raises(ZimFormatError):
        with ZimArchive(bad_file):
            pass


def test_09_html_paragraph_extractor_strips_scripts_and_noise():
    raw_html = """
    <html>
      <head><style>body { color: red; }</style><script>alert(1);</script></head>
      <body>
        <nav>Navigation menu noisy link</nav>
        <p>First clean encyclopedic paragraph with   extra   whitespace stripped cleanly.</p>
        <p>Tiny</p>
        <p>Second substantive technical paragraph describing cold storage thermal buffering.</p>
      </body>
    </html>
    """
    paras = extract_html_paragraphs(raw_html, min_chars=25)
    assert len(paras) == 2
    assert "alert(1)" not in " ".join(paras)
    assert "Navigation menu" not in " ".join(paras)
    assert paras[0] == "First clean encyclopedic paragraph with extra whitespace stripped cleanly."


def test_10_jsonl_fixture_loads_synthetic_reference_articles():
    raw_lines = [json.loads(line) for line in FIXTURE_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(raw_lines) == 8
    assert all(item.get("synthetic_non_training_fixture") is True for item in raw_lines)

    articles = list(iter_jsonl_articles(FIXTURE_PATH))
    assert len(articles) == 8
    assert articles[0].license == "CC BY-SA 4.0"


def test_11_html_directory_reader_ingests_local_files(tmp_path: Path):
    html_dir = tmp_path / "wiki_dump"
    html_dir.mkdir()
    (html_dir / "ferrite_core.html").write_text(
        "<html><head><title>Ferrite Core</title></head><body>"
        "<p>Ferrite transformer cores store non-volatile bits in woven sense wires.</p>"
        "</body></html>",
        encoding="utf-8",
    )
    items = list(iter_html_directory(html_dir))
    assert len(items) == 1
    assert items[0].slug == "ferrite_core"
    assert items[0].title == "Ferrite Core"


def test_12_simhash64_locality_sensitivity():
    a = "Standard wristwatches cut a miniature quartz tuning fork to vibrate at 32768 hertz."
    b = "Standard wristwatches cut a minature quarts tuning fork to vibrate at 32768 hertz."
    c = "Geothermal brine passes through a closed heat exchanger to vaporize organic isobutane."

    ha, hb, hc = compute_simhash64(a), compute_simhash64(b), compute_simhash64(c)
    assert hamming_distance64(ha, ha) == 0
    assert hamming_distance64(ha, hb) < hamming_distance64(ha, hc)


def test_13_char_trigram_jaccard_handles_typos():
    clean = "quartz piezoelectric oscillator 32768 hertz"
    typo = "quarts piezoeletric oscilator 32768 hertz"
    unrelated = "sandstone permafrost seed packets norway"

    assert char_trigram_jaccard(clean, typo) > 0.55
    assert char_trigram_jaccard(clean, unrelated) < 0.12


def test_14_indexer_streams_binary_zim_into_sqlite_fts5(tmp_path: Path):
    zim_path = tmp_path / "fixture.zim"
    db_path = tmp_path / "oracle.sqlite"
    build_mini_zim(zim_path, _sample_zim_specs(), compression=4)

    with OracleIndex(db_path) as idx:
        stats = idx.ingest_zim(zim_path, batch_size=4)
        assert stats["articles_indexed"] == 8
        assert stats["paragraphs_indexed"] == 24
        meta = idx.get_stats()
        assert meta["articles"] == 8
        assert meta["paragraphs"] == 24


def test_15_hybrid_search_exact_technical_fact(tmp_path: Path):
    db_path = tmp_path / "oracle.sqlite"
    with OracleIndex(db_path) as idx:
        idx.ingest_jsonl(FIXTURE_PATH)
        hits = idx.search("32768 hertz wristwatch tuning fork", top_k=3)
        assert len(hits) >= 1
        assert hits[0].slug == "quartz_piezoelectric_oscillator"
        assert hits[0].paragraph_index == 1


def test_16_hybrid_search_recovers_from_heavy_spelling_typos(tmp_path: Path):
    db_path = tmp_path / "oracle.sqlite"
    with OracleIndex(db_path) as idx:
        idx.ingest_jsonl(FIXTURE_PATH)
        hits = idx.search("piezoeletric quarts oscilator vibrat", top_k=3)
        assert len(hits) >= 1
        assert hits[0].slug == "quartz_piezoelectric_oscillator"


def test_17_hybrid_search_paraphrase_recall(tmp_path: Path):
    db_path = tmp_path / "oracle.sqlite"
    with OracleIndex(db_path) as idx:
        idx.ingest_jsonl(FIXTURE_PATH)
        hits = idx.search(
            "how submerged transoceanic glass cables amplify 1550nm light without electrical conversion",
            top_k=3,
        )
        assert len(hits) >= 1
        assert hits[0].slug == "submarine_erbium_fiber"
        assert hits[0].paragraph_index == 1


def test_18_verifier_accepts_authentic_verbatim_citation(tmp_path: Path):
    db_path = tmp_path / "oracle.sqlite"
    with OracleIndex(db_path) as idx:
        idx.ingest_jsonl(FIXTURE_PATH)
        verifier = CitationVerifier(idx)
        draft = (
            'The seed chambers stay frozen [svalbard_seed_vault#p1] '
            '"Mechanical refrigeration cools the surrounding permafrost chambers to minus 18 degrees Celsius".'
        )
        report = verifier.verify_text(draft)
        assert report.total_claims == 1
        assert report.verified_claims == 1
        assert report.verification_rate == 1.0
        assert "[UNVERIFIED AGAINST SOURCE]" not in report.annotated_text


def test_19_verifier_catches_fabricated_quote_numbers(tmp_path: Path):
    db_path = tmp_path / "oracle.sqlite"
    with OracleIndex(db_path) as idx:
        idx.ingest_jsonl(FIXTURE_PATH)
        verifier = CitationVerifier(idx)
        hallucinated = (
            'The chambers run colder [svalbard_seed_vault#p1] '
            '"Mechanical refrigeration cools the surrounding permafrost chambers to minus 45 degrees Celsius".'
        )
        report = verifier.verify_text(hallucinated)
        assert report.total_claims == 1
        assert report.verified_claims == 0
        assert report.results[0].reason == "QUOTE_NOT_VERBATIM"
        assert "[UNVERIFIED AGAINST SOURCE]" in report.annotated_text


def test_20_verifier_catches_invalid_paragraph_index(tmp_path: Path):
    db_path = tmp_path / "oracle.sqlite"
    with OracleIndex(db_path) as idx:
        idx.ingest_jsonl(FIXTURE_PATH)
        verifier = CitationVerifier(idx)
        bad_para = (
            'Claim with wrong paragraph [svalbard_seed_vault#p9] '
            '"Mechanical refrigeration cools the surrounding permafrost chambers".'
        )
        report = verifier.verify_text(bad_para)
        assert report.verified_claims == 0
        assert report.results[0].reason == "INVALID_PARAGRAPH_INDEX"


def test_21_verifier_catches_unknown_article_slug(tmp_path: Path):
    db_path = tmp_path / "oracle.sqlite"
    with OracleIndex(db_path) as idx:
        idx.ingest_jsonl(FIXTURE_PATH)
        verifier = CitationVerifier(idx)
        bad_slug = 'Imaginary source [atlantis_reactor#p0] "Infinite submarine power".'
        report = verifier.verify_text(bad_slug)
        assert report.verified_claims == 0
        assert report.results[0].reason == "UNKNOWN_ARTICLE"


def test_22_verifier_strict_strip_removes_unverified_sentences(tmp_path: Path):
    db_path = tmp_path / "oracle.sqlite"
    with OracleIndex(db_path) as idx:
        idx.ingest_jsonl(FIXTURE_PATH)
        verifier = CitationVerifier(idx)
        mixed = (
            'Valid claim [openzim_container#p1] "32-bit little-endian magic integer 72173914".\n'
            'Hallucinated claim [openzim_container#p1] "64-bit big-endian magic integer 99999999".'
        )
        report = verifier.verify_text(mixed, strict_strip=True)
        assert report.total_claims == 2
        assert report.verified_claims == 1
        assert "72173914" in report.clean_text
        assert "99999999" not in report.clean_text


def test_23_extractive_cpu_synthesizer_produces_100_percent_verified_citations(tmp_path: Path):
    db_path = tmp_path / "oracle.sqlite"
    with OracleIndex(db_path) as idx:
        idx.ingest_jsonl(FIXTURE_PATH)
        synth = OracleSynthesizer(idx)
        ans = synth.ask("How were binary ones and zeros encoded in Apollo core rope memory?")
        assert ans["mode"] == "extractive-cpu"
        assert ans["verification"]["verified_claims"] >= 1
        assert ans["verification"]["verification_rate"] == 1.0
        assert "apollo_core_rope_memory" in ans["answer"]


def test_24_local_127_0_0_1_llm_mode_enforces_loopback_and_verifies_hallucinations(tmp_path: Path):
    assert validate_local_llm_url("http://127.0.0.1:8080/v1/chat/completions") is True
    with pytest.raises(ValueError, match="ERR_NON_LOCAL_HOST"):
        validate_local_llm_url("https://api.example.com/v1/chat/completions")

    class MockLocal4BHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            payload = {
                "choices": [
                    {
                        "message": {
                            "content": (
                                'Verified fact [svalbard_seed_vault#p1] '
                                '"Mechanical refrigeration cools the surrounding permafrost chambers to minus 18 degrees Celsius".\n'
                                'Invented fact [svalbard_seed_vault#p1] '
                                '"Heated lava tubes warm the chambers to plus 50 degrees Celsius".'
                            )
                        }
                    }
                ]
            }
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            return

    mock_server = HTTPServer(("127.0.0.1", 0), MockLocal4BHandler)
    port = mock_server.server_address[1]
    thread = threading.Thread(target=mock_server.serve_forever, daemon=True)
    thread.start()

    try:
        db_path = tmp_path / "oracle.sqlite"
        with OracleIndex(db_path) as idx:
            idx.ingest_jsonl(FIXTURE_PATH)
            synth = OracleSynthesizer(
                idx,
                llm_endpoint=f"http://127.0.0.1:{port}/v1/chat/completions",
            )
            ans = synth.ask("What temperature is the Svalbard seed vault?")
            assert ans["mode"] == "local-llm-127.0.0.1"
            assert ans["verification"]["total_claims"] == 2
            assert ans["verification"]["verified_claims"] == 1
            assert "[UNVERIFIED AGAINST SOURCE]" in ans["annotated_answer"]
            assert "plus 50 degrees" not in ans["verified_only_answer"]
    finally:
        mock_server.shutdown()
        mock_server.server_close()


def test_25_benchmark_receipt_enforces_bounded_ram_and_full_verification(tmp_path: Path):
    receipt = run_benchmark(work_dir=tmp_path, fixture_jsonl=FIXTURE_PATH)
    assert receipt["zim_magic_verified"] == 72173914
    assert receipt["peak_heap_mib"] < 64.0
    assert receipt["process_rss_mib"] > 0.0
    assert receipt["index_disk_bytes"] > 0
    assert receipt["query_p50_ms"] >= 0.0
    assert receipt["query_p95_ms"] >= receipt["query_p50_ms"]
    assert receipt["citation_verification_rate"] == 1.0


def test_26_local_web_ui_and_api_bind_to_127_0_0_1_with_oddly_proven_palette(tmp_path: Path):
    db_path = tmp_path / "web_oracle.sqlite"
    with OracleIndex(db_path) as idx:
        idx.ingest_jsonl(FIXTURE_PATH)

    handle = start_oracle_server(db_path=db_path, host="127.0.0.1", port=0)
    try:
        with urlopen(f"http://127.0.0.1:{handle.port}/") as resp:
            assert resp.status == 200
            html = resp.read().decode("utf-8")
            assert "Offline Oracle" in html
            assert "#0E1116" in html
            assert "#88D445" in html
            allowed_palette = {
                "#0E1116",
                "#151B24",
                "#273244",
                "#F7F5EF",
                "#B6B4AD",
                "#88D445",
                "#F0592A",
                "#3B82F6",
                "#E5484D",
            }
            import re as _re
            for hex_code in _re.findall(r"#[0-9A-Fa-f]{6}", html):
                assert hex_code.upper() in allowed_palette

        req = Request(
            f"http://127.0.0.1:{handle.port}/api/ask",
            data=json.dumps({"query": "What is the magic number of an OpenZIM file?"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(req) as api_resp:
            assert api_resp.status == 200
            payload = json.loads(api_resp.read().decode("utf-8"))
            assert payload["verification"]["verified_claims"] >= 1
            assert any(s["slug"] == "openzim_container" for s in payload["sources"])
    finally:
        handle.close()
