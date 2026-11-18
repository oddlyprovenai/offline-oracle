# generated-by: gemini-ultra
from __future__ import annotations

import re
from dataclasses import dataclass

from offline_oracle.indexer import OracleIndex

CITATION_PATTERN = re.compile(r'\[([a-zA-Z0-9_-]+)#p(\d+)\]\s*"([^"]+)"')


def _canonical_span(text: str) -> str:
    cleaned = text.replace("\u201c", '"').replace("\u201d", '"').replace("\u2019", "'")
    cleaned = re.sub(r"\s+", " ", cleaned).strip().strip(".,;:")
    return cleaned.lower()


@dataclass(frozen=True)
class ClaimCheckResult:
    slug: str
    paragraph_index: int
    quoted_span: str
    verified: bool
    reason: str
    source_title: str | None = None
    source_license: str | None = None


@dataclass(frozen=True)
class VerificationReport:
    total_claims: int
    verified_claims: int
    verification_rate: float
    results: list[ClaimCheckResult]
    annotated_text: str
    clean_text: str


class CitationVerifier:
    """
    Zero-Hallucination Span Verifier:
    Confirms every [article_slug#pN] "verbatim quote" exists in SQLite and appears
    verbatim inside that exact paragraph.
    """

    def __init__(self, index: OracleIndex) -> None:
        self.index = index

    def check_single_claim(self, slug: str, paragraph_index: int, quoted_span: str) -> ClaimCheckResult:
        if not self.index.has_article(slug):
            return ClaimCheckResult(
                slug=slug,
                paragraph_index=paragraph_index,
                quoted_span=quoted_span,
                verified=False,
                reason="UNKNOWN_ARTICLE",
            )

        row = self.index.get_paragraph(slug, paragraph_index)
        if row is None:
            return ClaimCheckResult(
                slug=slug,
                paragraph_index=paragraph_index,
                quoted_span=quoted_span,
                verified=False,
                reason="INVALID_PARAGRAPH_INDEX",
            )

        norm_quote = _canonical_span(quoted_span)
        norm_para = _canonical_span(str(row["text"]))
        if len(norm_quote) < 6 or norm_quote not in norm_para:
            return ClaimCheckResult(
                slug=slug,
                paragraph_index=paragraph_index,
                quoted_span=quoted_span,
                verified=False,
                reason="QUOTE_NOT_VERBATIM",
                source_title=str(row["title"]),
                source_license=str(row["license"]),
            )

        return ClaimCheckResult(
            slug=slug,
            paragraph_index=paragraph_index,
            quoted_span=quoted_span,
            verified=True,
            reason="VERIFIED_VERBATIM",
            source_title=str(row["title"]),
            source_license=str(row["license"]),
        )

    def verify_text(self, text: str, strict_strip: bool = False) -> VerificationReport:
        lines = text.splitlines() if text else []
        results: list[ClaimCheckResult] = []
        annotated_lines: list[str] = []
        clean_lines: list[str] = []

        for line in lines:
            matches = list(CITATION_PATTERN.finditer(line))
            if not matches:
                annotated_lines.append(line)
                if not strict_strip and line.strip():
                    clean_lines.append(line)
                continue

            line_all_ok = True
            line_reasons: list[str] = []
            for m in matches:
                slug = m.group(1)
                p_idx = int(m.group(2))
                quote = m.group(3)
                res = self.check_single_claim(slug, p_idx, quote)
                results.append(res)
                if not res.verified:
                    line_all_ok = False
                    line_reasons.append(res.reason)

            if line_all_ok:
                annotated_lines.append(line)
                clean_lines.append(line)
            else:
                tag = f"[UNVERIFIED AGAINST SOURCE] ({', '.join(line_reasons)})"
                annotated_lines.append(f"{tag} {line}")
                if not strict_strip:
                    clean_lines.append(f"{tag} {line}")

        total = len(results)
        verified_n = sum(1 for r in results if r.verified)
        rate = (verified_n / total) if total > 0 else 0.0

        return VerificationReport(
            total_claims=total,
            verified_claims=verified_n,
            verification_rate=round(rate, 4),
            results=results,
            annotated_text="\n".join(annotated_lines),
            clean_text="\n".join(clean_lines),
        )
