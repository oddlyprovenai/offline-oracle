<!-- generated-by: gemini-ultra -->

# Offline Oracle (`oddly-offline-oracle`)

> *"No Wi-Fi. Still answers. With sources."*

**Offline Oracle** (`oddly-offline-oracle`) is a 100% offline research assistant built by **Oddly Proven Lab** for the *16 GB Challenge*. It streams binary **OpenZIM (`.zim`)** archives (such as Kiwix Wikipedia and StackExchange dumps) or local JSONL/HTML folders into a bounded-RAM **SQLite FTS5 (BM25) + 64-bit SimHash / character 3-gram hybrid index**, answers questions via either a pure-CPU extractive synthesizer or a local `127.0.0.1` 4B model, and **verifies every cited quote verbatim against the source paragraph**.

Zero runtime third-party Python dependencies (`dependencies = []` in `pyproject.toml`). Zero telemetry.

---

## How `oddly-offline-oracle` Differs from Existing Offline Kiwix RAG Tools

| Project | URL | Stack | Limitation | How `oddly-offline-oracle` Differentiates |
|---|---|---|---|---|
| **`amih/kiwix-rag`** | https://github.com/amih/kiwix-rag | Node.js + external `kiwix-serve` + Ollama | Requires running `kiwix-serve`; fetches lead summaries without paragraph span verification | Native Python stdlib binary `.zim` (`0x044D495A`) `mmap` reader, `[slug#pN]` paragraph indexing, and verbatim substring verification (`verifier.py`). |
| **`macromeer/offline-wikipedia-rag`** | https://github.com/macromeer/offline-wikipedia-rag | Python + dense vector embeddings | Heavy embedding pass and RAM footprint on 16 GB laptops | Zero neural embedding models; streams batches into SQLite FTS5 + 64-bit SimHash + char 3-gram reranker (`8.618 MiB` measured peak Python heap on demo bench). |
| **`mozanunal/llm-tools-kiwix`** | https://github.com/mozanunal/llm-tools-kiwix | Simon Willison `llm` CLI plugin + `libzim` | Relies on LLM tool calls without deterministic quote auditing or pure-CPU fallback | Built-in `CitationVerifier` flags `[UNVERIFIED AGAINST SOURCE]` on fabricated numbers/quotes + pure-CPU extractive mode when no LLM server is running. |
| **`rouralberto/zim-llm`** | https://github.com/rouralberto/zim-llm | Python + LangChain + `sentence-transformers` + ChromaDB/FAISS | Large dependency tree and vector DB storage overhead | Pure Python stdlib (`sqlite3`, `mmap`, `zlib`, `lzma`, `compression.zstd`, `tracemalloc`) + built-in `oracle bench` hardware receipt. |
| **Kiwix (`kiwix-tools`)** | https://github.com/kiwix/kiwix-tools | Official C++ ZIM reader & server | Keyword browsing without multi-paragraph cited synthesis | Reads standard OpenZIM archives and pairs answered claims with clickable highlighted source paragraphs on `127.0.0.1`. |

---

## Quickstart (Zero `pip install` Runtime Dependencies)

Requires Python `>= 3.11` with SQLite FTS5 enabled (tested on `Python 3.14.7`, including stdlib Zstandard cluster support via `compression.zstd`).

```text
lab@mac-studio ~ % git clone https://github.com/oddlyprovenai/offline-oracle.git
lab@mac-studio ~ % cd offline-oracle
lab@mac-studio ~ % PYTHONPATH=src python3 -m offline_oracle.cli build-demo-zim --output demo.zim
Built OpenZIM archive: demo.zim (3347 bytes, 8 articles)

lab@mac-studio ~ % PYTHONPATH=src python3 -m offline_oracle.cli index --source demo.zim --db demo.sqlite
Indexed 8 articles (24 paragraphs) -> 69632 bytes on disk.

lab@mac-studio ~ % PYTHONPATH=src python3 -m offline_oracle.cli ask --db demo.sqlite "Why do wristwatches vibrate a quartz crystal at 32768 hertz?"
Quartz Piezoelectric Oscillator [quartz_piezoelectric_oscillator#p1] "Standard wristwatches cut a miniature quartz tuning fork to vibrate at 32768 hertz, chosen because 32768 equals two raised to the fifteenth power and divides cleanly down to a one-hertz pulse via fifteen flip-flop stages".
Quartz Piezoelectric Oscillator [quartz_piezoelectric_oscillator#p2] "Temperature-compensated crystal oscillators stabilize resonant drift across industrial operating ranges within parts per million".

[Receipt: 2/2 citations verbatim-verified | mode=extractive-cpu | 1.104 ms | CC BY-SA 4.0]
```

Launch the split-pane offline Web UI (`127.0.0.1` loopback only, zero external CDNs):

```text
lab@mac-studio ~ % PYTHONPATH=src python3 -m offline_oracle.cli serve --db demo.sqlite --host 127.0.0.1 --port 8765
Offline Oracle UI listening on http://127.0.0.1:8765/ (Ctrl+C to stop)
```

Connect an optional local 4B OpenAI-compatible server (e.g., SPIKE or `llama.cpp` on `127.0.0.1`)—every generated sentence still passes through `verifier.py`:

```text
lab@mac-studio ~ % PYTHONPATH=src python3 -m offline_oracle.cli ask --db demo.sqlite \
  --llm-url http://127.0.0.1:8080/v1/chat/completions \
  "What temperature are the Svalbard seed chambers cooled to?"
```

---

## Measured Hardware & Memory Receipt (`oracle bench`)

Measured locally on Apple Silicon (`Python 3.14.7`, air-gapped):

```text
lab@mac-studio ~ % PYTHONPATH=src python3 -m offline_oracle.cli bench
=== OFFLINE ORACLE // 16 GB CHALLENGE RECEIPT ===
Python stdlib runtime    : 3.14.7
OpenZIM magic verified   : 72173914 (0x044D495A)
Archive -> SQLite index  : 3347 B -> 69632 B (24 paragraphs in 97.614 ms)
Peak Python heap (trace) : 8824.42 KiB (8.618 MiB)
Process max RSS          : 33.73 MiB
Query latency P50 / P95  : 4.024 ms / 4.59 ms
Verbatim citation verify : 16/16 (100.0%)
Outbound network calls   : 0 (air-gapped / 127.0.0.1 only)
```

---

## How Zero-Hallucination Span Verification Works (`verifier.py`)

Every factual claim must carry `[article_slug#pN] "exact verbatim quote"`. Before returning an answer, `CitationVerifier` checks SQLite for three invariants:

1. `article_slug` exists in `articles` (otherwise `UNKNOWN_ARTICLE`).
2. Paragraph `pN` exists for that slug in `chunks` (otherwise `INVALID_PARAGRAPH_INDEX`).
3. The quoted text appears **verbatim** (after canonical whitespace normalization) inside `pN` (otherwise `QUOTE_NOT_VERBATIM`).

Any failed claim is flagged `[UNVERIFIED AGAINST SOURCE]` (or stripped cleanly in `strict_strip=True` mode).

---

## Running Tests

```text
lab@mac-studio ~ % PYTHONPATH=src python3 -m pytest -v
============================== 26 passed in 1.52s ==============================
```

---

## Licensing, Attribution & Trademark Notice

- **Engine License**: Apache-2.0 (`LICENSE`).
- **Archive Content License Preservation**: Offline Oracle preserves per-article license metadata (such as **CC BY-SA 4.0** for Wikipedia and StackExchange `.zim` archives distributed via Kiwix) in both CLI receipts and the Web UI. Note that `fixtures/synthetic_encyclopedia.jsonl` contains 8 tiny synthetic reference paragraphs (`"synthetic_non_training_fixture": true`) solely for unit testing—not an LLM training dataset.
- **Non-Affiliation Disclaimer**: `oddly-offline-oracle` is an independent open-source project by Oddly Proven Lab and is **not affiliated with, endorsed by, or sponsored by Oracle Corporation**.
