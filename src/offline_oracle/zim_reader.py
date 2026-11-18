# generated-by: gemini-ultra
from __future__ import annotations

import hashlib
import json
import lzma
import mmap
import re
import struct
import uuid
import zlib
from collections import OrderedDict
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterator

try:
    from compression import zstd as _stdlib_zstd
except ImportError:  # pragma: no cover
    _stdlib_zstd = None

ZIM_MAGIC = 72173914  # 0x044D495A little-endian ("ZIM\x04")
ZIM_HEADER_STRUCT = struct.Struct("<IHH16sIIQQQQIIQ")  # 80 bytes
REDIRECT_MIMETYPE = 0xFFFF
MAX_CLUSTER_DECOMPRESSED_BYTES = 32 * 1024 * 1024  # 32 MiB anti-bomb cap


class ZimFormatError(ValueError):
    """Raised when a binary ZIM container violates OpenZIM framing or size caps."""


@dataclass(frozen=True)
class ZimHeader:
    magic_number: int
    major_version: int
    minor_version: int
    archive_uuid: bytes
    entry_count: int
    cluster_count: int
    url_ptr_pos: int
    title_ptr_pos: int
    cluster_ptr_pos: int
    mime_list_pos: int
    main_page: int
    layout_page: int
    checksum_pos: int


@dataclass(frozen=True)
class ArticleRecord:
    slug: str
    title: str
    paragraphs: list[str]
    url: str = ""
    license: str = "CC BY-SA 4.0"


class _CleanParagraphHTMLParser(HTMLParser):
    SKIP_TAGS = {"script", "style", "nav", "footer", "header", "noscript", "aside", "svg"}
    BLOCK_TAGS = {"p", "li", "blockquote", "dd"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._in_title = False
        self._in_block = False
        self._current: list[str] = []
        self.title_parts: list[str] = []
        self.paragraphs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        t = tag.lower()
        if t in self.SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth > 0:
            return
        if t == "title":
            self._in_title = True
        elif t in self.BLOCK_TAGS:
            self._flush_block()
            self._in_block = True

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t in self.SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
            return
        if t == "title":
            self._in_title = False
        elif t in self.BLOCK_TAGS and self._in_block:
            self._flush_block()
            self._in_block = False

    def handle_data(self, data: str) -> None:
        if self._skip_depth > 0:
            return
        if self._in_title:
            self.title_parts.append(data)
        elif self._in_block:
            self._current.append(data)

    def _flush_block(self) -> None:
        if not self._current:
            return
        joined = re.sub(r"\s+", " ", "".join(self._current)).strip()
        self._current.clear()
        if joined:
            self.paragraphs.append(joined)


def extract_html_paragraphs(html_text: str, min_chars: int = 20) -> list[str]:
    parser = _CleanParagraphHTMLParser()
    parser.feed(html_text)
    parser._flush_block()
    return [p for p in parser.paragraphs if len(p) >= min_chars]


def slugify_url(url_or_name: str) -> str:
    stem = re.sub(r"\.(html?|xhtml|txt)$", "", url_or_name.strip(), flags=re.IGNORECASE)
    stem = stem.strip("/").replace("/", "__")
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "_", stem).strip("_").lower()
    return cleaned or "article"


def _read_c_string(buf: mmap.mmap | bytes, offset: int) -> tuple[str, int]:
    end = buf.find(b"\x00", offset)
    if end == -1:
        raise ZimFormatError("Unterminated null-delimited string in ZIM directory")
    return bytes(buf[offset:end]).decode("utf-8", errors="replace"), end + 1


def _decompress_bounded(raw: bytes, comp_type: int) -> bytes:
    if comp_type in (0, 1):
        if len(raw) > MAX_CLUSTER_DECOMPRESSED_BYTES:
            raise ZimFormatError("Uncompressed cluster exceeds 32 MiB cap")
        return raw
    if comp_type == 2:
        decomp = zlib.decompressobj()
        out = decomp.decompress(raw, MAX_CLUSTER_DECOMPRESSED_BYTES + 1)
        if len(out) > MAX_CLUSTER_DECOMPRESSED_BYTES or decomp.unconsumed_tail:
            raise ZimFormatError("zlib cluster exceeds 32 MiB cap")
        return out
    if comp_type == 4:
        decomp_lz = lzma.LZMADecompressor()
        out = decomp_lz.decompress(raw, max_length=MAX_CLUSTER_DECOMPRESSED_BYTES + 1)
        if len(out) > MAX_CLUSTER_DECOMPRESSED_BYTES or not decomp_lz.eof:
            raise ZimFormatError("LZMA2 cluster exceeds 32 MiB cap")
        return out
    if comp_type == 5:
        if _stdlib_zstd is None:
            raise ZimFormatError("Zstandard cluster encountered without stdlib zstd support")
        out = _stdlib_zstd.decompress(raw)
        if len(out) > MAX_CLUSTER_DECOMPRESSED_BYTES:
            raise ZimFormatError("Zstandard cluster exceeds 32 MiB cap")
        return out
    raise ZimFormatError(f"Unsupported OpenZIM cluster compression code: {comp_type}")


class ZimArchive:
    """Streaming mmap-backed OpenZIM v5/v6 reader with bounded 4-cluster LRU cache."""

    def __init__(self, path: str | Path, cluster_cache_size: int = 4) -> None:
        self.path = Path(path)
        self._file = self.path.open("rb")
        try:
            self._mm = mmap.mmap(self._file.fileno(), 0, access=mmap.ACCESS_READ)
            if len(self._mm) < ZIM_HEADER_STRUCT.size:
                raise ZimFormatError("File smaller than 80-byte OpenZIM header")
            fields = ZIM_HEADER_STRUCT.unpack_from(self._mm, 0)
            self.header = ZimHeader(*fields)
            if self.header.magic_number != ZIM_MAGIC:
                raise ZimFormatError(
                    f"Invalid OpenZIM magic {self.header.magic_number} (expected {ZIM_MAGIC})"
                )
            self.mime_types = self._parse_mime_list()
            self._cluster_cache: OrderedDict[int, tuple[bool, bytes]] = OrderedDict()
            self._cluster_cache_size = max(1, cluster_cache_size)
        except Exception:
            self._file.close()
            raise

    def close(self) -> None:
        if hasattr(self, "_mm") and not self._mm.closed:
            self._mm.close()
        if not self._file.closed:
            self._file.close()

    def __enter__(self) -> "ZimArchive":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _parse_mime_list(self) -> list[str]:
        mimes: list[str] = []
        pos = self.header.mime_list_pos
        while pos < len(self._mm):
            if self._mm[pos] == 0:
                break
            mime_str, pos = _read_c_string(self._mm, pos)
            mimes.append(mime_str)
        return mimes

    def _get_url_entry_offset(self, index: int) -> int:
        if index < 0 or index >= self.header.entry_count:
            raise ZimFormatError(f"Entry index out of bounds: {index}")
        ptr_offset = self.header.url_ptr_pos + index * 8
        return struct.unpack_from("<Q", self._mm, ptr_offset)[0]

    def _get_cluster_bounds(self, cluster_index: int) -> tuple[int, int]:
        if cluster_index < 0 or cluster_index >= self.header.cluster_count:
            raise ZimFormatError(f"Cluster index out of bounds: {cluster_index}")
        start = struct.unpack_from("<Q", self._mm, self.header.cluster_ptr_pos + cluster_index * 8)[0]
        if cluster_index + 1 < self.header.cluster_count:
            end = struct.unpack_from(
                "<Q", self._mm, self.header.cluster_ptr_pos + (cluster_index + 1) * 8
            )[0]
        else:
            end = self.header.checksum_pos if self.header.checksum_pos > start else len(self._mm)
        return start, end

    def _read_cluster_payload(self, cluster_index: int) -> tuple[bool, bytes]:
        if cluster_index in self._cluster_cache:
            self._cluster_cache.move_to_end(cluster_index)
            return self._cluster_cache[cluster_index]

        start, end = self._get_cluster_bounds(cluster_index)
        info_byte = self._mm[start]
        comp_type = info_byte & 0x0F
        is_extended = bool(info_byte & 0x10)
        raw_stream = bytes(self._mm[start + 1 : end])
        payload = _decompress_bounded(raw_stream, comp_type)

        self._cluster_cache[cluster_index] = (is_extended, payload)
        while len(self._cluster_cache) > self._cluster_cache_size:
            self._cluster_cache.popitem(last=False)
        return is_extended, payload

    def _read_blob(self, cluster_index: int, blob_index: int) -> bytes:
        is_extended, payload = self._read_cluster_payload(cluster_index)
        offset_width = 8 if is_extended else 4
        fmt = "<Q" if is_extended else "<I"
        first_offset = struct.unpack_from(fmt, payload, 0)[0]
        blob_count = (first_offset // offset_width) - 1
        if blob_index < 0 or blob_index >= blob_count:
            raise ZimFormatError(f"Blob index {blob_index} out of bounds ({blob_count})")
        b_start = struct.unpack_from(fmt, payload, blob_index * offset_width)[0]
        b_end = struct.unpack_from(fmt, payload, (blob_index + 1) * offset_width)[0]
        return payload[b_start:b_end]

    def _parse_directory_entry(self, entry_offset: int) -> dict:
        mime_idx, param_len, ns_byte, revision = struct.unpack_from("<HBBI", self._mm, entry_offset)
        namespace = chr(ns_byte)
        if mime_idx == REDIRECT_MIMETYPE:
            redirect_target = struct.unpack_from("<I", self._mm, entry_offset + 8)[0]
            url, next_pos = _read_c_string(self._mm, entry_offset + 12)
            title, _ = _read_c_string(self._mm, next_pos)
            return {
                "kind": "redirect",
                "namespace": namespace,
                "url": url,
                "title": title or url,
                "redirect_index": redirect_target,
            }

        cluster_num, blob_num = struct.unpack_from("<II", self._mm, entry_offset + 8)
        url, next_pos = _read_c_string(self._mm, entry_offset + 16)
        title, _ = _read_c_string(self._mm, next_pos)
        mime_name = self.mime_types[mime_idx] if mime_idx < len(self.mime_types) else "application/octet-stream"
        return {
            "kind": "content",
            "mime": mime_name,
            "namespace": namespace,
            "revision": revision,
            "cluster": cluster_num,
            "blob": blob_num,
            "url": url,
            "title": title or url,
            "param_len": param_len,
        }

    def _resolve_entry(self, index: int, max_hops: int = 8) -> dict | None:
        curr_idx = index
        for _ in range(max_hops):
            entry = self._parse_directory_entry(self._get_url_entry_offset(curr_idx))
            if entry["kind"] == "content":
                return entry
            curr_idx = entry["redirect_index"]
        return None

    def read_entry_by_url(self, target_url: str, namespace: str | None = None) -> ArticleRecord | None:
        # Binary search over URL-sorted directory pointers if namespace provided, else fast scan/bsearch
        lo, hi = 0, self.header.entry_count - 1
        candidates = ["C", "A"] if namespace is None else [namespace]
        for ns in candidates:
            lo, hi = 0, self.header.entry_count - 1
            target_key = f"{ns}/{target_url}"
            while lo <= hi:
                mid = (lo + hi) // 2
                entry = self._parse_directory_entry(self._get_url_entry_offset(mid))
                entry_key = f"{entry['namespace']}/{entry['url']}"
                if entry_key == target_key:
                    resolved = self._resolve_entry(mid)
                    if resolved is None:
                        return None
                    return self._entry_to_article(resolved)
                if entry_key < target_key:
                    lo = mid + 1
                else:
                    hi = mid - 1
        return None

    def _entry_to_article(self, entry: dict) -> ArticleRecord | None:
        if not entry["mime"].startswith("text/html") and not entry["mime"].startswith("text/plain"):
            return None
        raw_bytes = self._read_blob(entry["cluster"], entry["blob"])
        text = raw_bytes.decode("utf-8", errors="replace")
        paragraphs = (
            extract_html_paragraphs(text)
            if "html" in entry["mime"]
            else [p.strip() for p in text.split("\n\n") if len(p.strip()) >= 20]
        )
        if not paragraphs:
            return None
        return ArticleRecord(
            slug=slugify_url(entry["url"]),
            title=entry["title"],
            paragraphs=paragraphs,
            url=entry["url"],
            license="CC BY-SA 4.0",
        )

    def iter_articles(self) -> Iterator[ArticleRecord]:
        for idx in range(self.header.entry_count):
            entry = self._parse_directory_entry(self._get_url_entry_offset(idx))
            if entry["kind"] != "content":
                continue
            if entry["namespace"] not in ("C", "A"):
                continue
            art = self._entry_to_article(entry)
            if art is not None:
                yield art


def build_mini_zim(
    output_path: str | Path,
    articles: list[dict],
    compression: int = 4,
    extended_cluster: bool = False,
    redirects: list[dict] | None = None,
    cluster_batch_size: int = 4,
) -> Path:
    """
    Build a spec-compliant OpenZIM v6 binary file (magic 72173914 / 0x044D495A)
    for offline deterministic testing and compact demo archives.
    """
    out = Path(output_path)
    redirects = redirects or []
    mime_types = ["text/html"]
    mime_blob = b"".join(m.encode("utf-8") + b"\x00" for m in mime_types) + b"\x00"

    # Group content blobs into clusters
    clusters_raw: list[bytes] = []
    content_locations: list[tuple[int, int]] = []
    for c_idx, start_i in enumerate(range(0, len(articles), max(1, cluster_batch_size))):
        batch = articles[start_i : start_i + max(1, cluster_batch_size)]
        encoded_blobs = [item["html"].encode("utf-8") for item in batch]
        for b_idx in range(len(encoded_blobs)):
            content_locations.append((c_idx, b_idx))

        offset_width = 8 if extended_cluster else 4
        fmt = "<Q" if extended_cluster else "<I"
        table_bytes = (len(encoded_blobs) + 1) * offset_width
        offsets = [table_bytes]
        for b in encoded_blobs:
            offsets.append(offsets[-1] + len(b))
        uncompressed_cluster = b"".join(struct.pack(fmt, off) for off in offsets) + b"".join(encoded_blobs)

        if compression in (0, 1):
            comp_stream = uncompressed_cluster
            comp_code = 1
        elif compression == 2:
            comp_stream = zlib.compress(uncompressed_cluster, level=6)
            comp_code = 2
        elif compression == 4:
            comp_stream = lzma.compress(uncompressed_cluster, preset=1)
            comp_code = 4
        elif compression == 5:
            if _stdlib_zstd is None:
                raise ZimFormatError("Python stdlib zstd unavailable")
            comp_stream = _stdlib_zstd.compress(uncompressed_cluster)
            comp_code = 5
        else:
            raise ZimFormatError(f"Unsupported compression code: {compression}")

        info_byte = comp_code | (0x10 if extended_cluster else 0x00)
        clusters_raw.append(bytes([info_byte]) + comp_stream)

    # Prepare combined directory records sorted by "<namespace>/<url>"
    combined_records: list[dict] = []
    for idx, item in enumerate(articles):
        ns = item.get("namespace", "C")
        c_num, b_num = content_locations[idx]
        combined_records.append(
            {
                "kind": "content",
                "orig_content_idx": idx,
                "namespace": ns,
                "url": item["url"],
                "title": item["title"],
                "cluster": c_num,
                "blob": b_num,
            }
        )

    for red in redirects:
        combined_records.append(
            {
                "kind": "redirect",
                "namespace": red.get("namespace", "C"),
                "url": red["url"],
                "title": red.get("title", red["url"]),
                "orig_target_content_idx": red["target_index"],
            }
        )

    combined_records.sort(key=lambda r: f"{r['namespace']}/{r['url']}")
    content_idx_to_sorted_idx = {
        rec["orig_content_idx"]: sorted_i
        for sorted_i, rec in enumerate(combined_records)
        if rec["kind"] == "content"
    }

    dir_blobs: list[bytes] = []
    for rec in combined_records:
        ns_byte = ord(rec["namespace"][0])
        url_b = rec["url"].encode("utf-8") + b"\x00"
        title_b = rec["title"].encode("utf-8") + b"\x00"
        if rec["kind"] == "redirect":
            target_sorted = content_idx_to_sorted_idx[rec["orig_target_content_idx"]]
            entry_bin = struct.pack("<HBBII", REDIRECT_MIMETYPE, 0, ns_byte, 0, target_sorted) + url_b + title_b
        else:
            entry_bin = (
                struct.pack("<HBBIII", 0, 0, ns_byte, 0, rec["cluster"], rec["blob"])
                + url_b
                + title_b
            )
        dir_blobs.append(entry_bin)

    entry_count = len(combined_records)
    cluster_count = len(clusters_raw)

    mime_list_pos = ZIM_HEADER_STRUCT.size
    url_ptr_pos = mime_list_pos + len(mime_blob)
    title_ptr_pos = url_ptr_pos + entry_count * 8
    dir_entries_pos = title_ptr_pos + entry_count * 4

    entry_offsets: list[int] = []
    cursor = dir_entries_pos
    for b in dir_blobs:
        entry_offsets.append(cursor)
        cursor += len(b)

    cluster_ptr_pos = cursor
    cluster_data_pos = cluster_ptr_pos + cluster_count * 8
    cluster_offsets: list[int] = []
    c_cursor = cluster_data_pos
    for c_blob in clusters_raw:
        cluster_offsets.append(c_cursor)
        c_cursor += len(c_blob)

    checksum_pos = c_cursor
    title_order = sorted(range(entry_count), key=lambda i: combined_records[i]["title"])

    archive_uuid = uuid.uuid5(uuid.NAMESPACE_DNS, "oddly-offline-oracle.local").bytes
    header_bytes = ZIM_HEADER_STRUCT.pack(
        ZIM_MAGIC,
        6,
        1,
        archive_uuid,
        entry_count,
        cluster_count,
        url_ptr_pos,
        title_ptr_pos,
        cluster_ptr_pos,
        mime_list_pos,
        0xFFFFFFFF,
        0xFFFFFFFF,
        checksum_pos,
    )

    body = bytearray()
    body.extend(header_bytes)
    body.extend(mime_blob)
    for off in entry_offsets:
        body.extend(struct.pack("<Q", off))
    for idx in title_order:
        body.extend(struct.pack("<I", idx))
    for b in dir_blobs:
        body.extend(b)
    for c_off in cluster_offsets:
        body.extend(struct.pack("<Q", c_off))
    for c_blob in clusters_raw:
        body.extend(c_blob)

    digest = hashlib.md5(body).digest()
    body.extend(digest)
    out.write_bytes(bytes(body))
    return out


def iter_jsonl_articles(jsonl_path: str | Path) -> Iterator[ArticleRecord]:
    path = Path(jsonl_path)
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            stripped = line.strip()
            if not stripped:
                continue
            obj = json.loads(stripped)
            paragraphs = [
                re.sub(r"\s+", " ", p).strip()
                for p in obj.get("paragraphs", [])
                if len(str(p).strip()) >= 15
            ]
            if not paragraphs:
                continue
            slug = slugify_url(obj.get("slug") or obj.get("title") or "article")
            yield ArticleRecord(
                slug=slug,
                title=str(obj.get("title") or slug),
                paragraphs=paragraphs,
                url=str(obj.get("url") or f"{slug}.html"),
                license=str(obj.get("license") or "CC BY-SA 4.0"),
            )


def iter_html_directory(directory_path: str | Path) -> Iterator[ArticleRecord]:
    root = Path(directory_path)
    for html_file in sorted(root.rglob("*.htm*")):
        raw = html_file.read_text(encoding="utf-8", errors="replace")
        parser = _CleanParagraphHTMLParser()
        parser.feed(raw)
        parser._flush_block()
        paragraphs = [p for p in parser.paragraphs if len(p) >= 15]
        if not paragraphs:
            continue
        title = re.sub(r"\s+", " ", "".join(parser.title_parts)).strip() or html_file.stem
        yield ArticleRecord(
            slug=slugify_url(html_file.stem),
            title=title,
            paragraphs=paragraphs,
            url=html_file.name,
            license="CC BY-SA 4.0",
        )
