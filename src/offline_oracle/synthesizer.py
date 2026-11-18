# generated-by: gemini-ultra
from __future__ import annotations

import json
import re
import time
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from offline_oracle.indexer import OracleIndex, SearchHit, extract_char_trigrams
from offline_oracle.verifier import CitationVerifier


def validate_local_llm_url(url: str) -> bool:
    parsed = urlparse(url)
    allowed_hosts = {"127.0.0.1", "localhost", "::1"}
    if parsed.scheme != "http" or parsed.hostname not in allowed_hosts:
        raise ValueError(
            f"ERR_NON_LOCAL_HOST: Refusing non-loopback endpoint '{url}'. Only http://127.0.0.1 or http://localhost is allowed."
        )
    return True


def _select_salient_sentence(paragraph: str, query: str) -> str:
    sentences = [
        s.strip().rstrip(".")
        for s in re.split(r"(?<=[.!?])\s+", paragraph.strip())
        if len(s.strip()) >= 12
    ]
    if not sentences:
        return paragraph.strip().rstrip(".")

    q_grams = extract_char_trigrams(query)
    if not q_grams:
        return sentences[0]

    ranked = sorted(
        sentences,
        key=lambda sent: len(q_grams & extract_char_trigrams(sent)),
        reverse=True,
    )
    return ranked[0]


class OracleSynthesizer:
    """
    Dual-Mode Citation Synthesizer:
    1. Pure-CPU Extractive Mode (zero external model server, sub-10ms latency)
    2. Local 127.0.0.1 OpenAI-Compatible 4B Endpoint Mode (with mandatory Verifier gate)
    """

    def __init__(
        self,
        index: OracleIndex,
        llm_endpoint: str | None = None,
        model: str = "local-4b",
        top_k: int = 3,
        timeout_sec: float = 15.0,
    ) -> None:
        if llm_endpoint is not None:
            validate_local_llm_url(llm_endpoint)
        self.index = index
        self.verifier = CitationVerifier(index)
        self.llm_endpoint = llm_endpoint
        self.model = model
        self.top_k = top_k
        self.timeout_sec = timeout_sec

    def _synthesize_extractive(self, query: str, hits: list[SearchHit]) -> str:
        if not hits:
            return "No matching offline archive paragraphs found for this query."
        lines: list[str] = []
        for hit in hits[: min(2, len(hits))]:
            verbatim_span = _select_salient_sentence(hit.text, query)
            lines.append(f'{hit.title} [{hit.anchor}] "{verbatim_span}".')
        return "\n".join(lines)

    def _synthesize_local_llm(self, query: str, hits: list[SearchHit]) -> str:
        assert self.llm_endpoint is not None
        validate_local_llm_url(self.llm_endpoint)

        context_blocks = "\n\n".join(
            f"[{h.anchor}] ({h.title}, {h.license}):\n{h.text}" for h in hits
        )
        system_prompt = (
            "Answer using ONLY the supplied offline source paragraphs. "
            "Every factual sentence MUST include an inline citation and an exact verbatim quote "
            'formatted strictly as [article_slug#pN] "exact verbatim substring from that paragraph".'
        )
        payload = {
            "model": self.model,
            "temperature": 0.1,
            "max_tokens": 320,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Question: {query}\n\nSources:\n{context_blocks}"},
            ],
        }
        req = Request(
            self.llm_endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(req, timeout=self.timeout_sec) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return str(data["choices"][0]["message"]["content"]).strip()

    def ask(self, query: str) -> dict:
        t0 = time.perf_counter()
        hits = self.index.search(query, top_k=self.top_k)

        if self.llm_endpoint is not None and hits:
            raw_draft = self._synthesize_local_llm(query, hits)
            mode = "local-llm-127.0.0.1"
        else:
            raw_draft = self._synthesize_extractive(query, hits)
            mode = "extractive-cpu"

        annotated_report = self.verifier.verify_text(raw_draft, strict_strip=False)
        strict_report = self.verifier.verify_text(raw_draft, strict_strip=True)
        elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 3)

        return {
            "query": query,
            "mode": mode,
            "latency_ms": elapsed_ms,
            "answer": annotated_report.annotated_text,
            "annotated_answer": annotated_report.annotated_text,
            "verified_only_answer": strict_report.clean_text,
            "verification": {
                "total_claims": annotated_report.total_claims,
                "verified_claims": annotated_report.verified_claims,
                "verification_rate": annotated_report.verification_rate,
                "claims": [
                    {
                        "slug": c.slug,
                        "paragraph_index": c.paragraph_index,
                        "quoted_span": c.quoted_span,
                        "verified": c.verified,
                        "reason": c.reason,
                        "license": c.source_license,
                    }
                    for c in annotated_report.results
                ],
            },
            "sources": [
                {
                    "anchor": h.anchor,
                    "slug": h.slug,
                    "title": h.title,
                    "paragraph_index": h.paragraph_index,
                    "text": h.text,
                    "license": h.license,
                    "hybrid_score": h.hybrid_score,
                    "bm25_score": h.bm25_score,
                    "trigram_score": h.trigram_score,
                }
                for h in hits
            ],
        }
