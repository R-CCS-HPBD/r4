"""Knowledge Bundle Format (ADR-013, INV-13; ADR-032 amendment 2026-09 = v1.2;
ADR-093 D-12 = v1.3).

A knowledge bundle (``.kb``) is a self-contained, portable archive of a
project's knowledge base.  It can be exported from the platform and imported
into a local inference environment.

Canonical producer layout (r4-kb v1.2/v1.3, written by the umrs durable
export job ``KNOWLEDGE_EXPORT`` — flat, all JSONL)::

    my-project.kb/
    ├── manifest.json           # Bundle metadata, schema version, pins, tables
    ├── artifacts.jsonl         # knowledge_artifacts rows (metadata)
    ├── documents.jsonl         # knowledge_ir rows
    ├── nodes.jsonl             # knowledge_nodes rows (chunks: content.kind == chunk)
    ├── edges.jsonl             # knowledge_edges rows
    ├── embeddings.jsonl        # memory_vectors rows (legacy RAG store)
    ├── node_embeddings.jsonl   # knowledge_node_embeddings rows, keyed by node_id
    ├── catalog.jsonl           # catalog rows
    ├── a2l_links.jsonl         # A2L lineage links
    ├── a2l_refs.jsonl          # A2L artifact refs
    ├── raw_objects.jsonl       # optional (include_raw): raw/<sha256> objects
    └── signature.sig

A chunk node stores its offsets, an md5 and a 500-character excerpt. v1.3
(``KNOWLEDGE_EXPORT --chunk-text``, by explicit opt-in) adds the md5-verified
chunk text to each chunk node. :meth:`KnowledgeBundleReader.read_chunks`
gives every chunk node one flat row whose text is, in order: the node's own
text (v1.3, or a canonized legacy chunk), the md5-verified slice of the
artifact's carried raw object, else the excerpt — ``text_source`` says which.

The manifest carries format ``r4-kb``, version ``1.2`` or ``1.3``, an
``embedding`` pin (model + dimensions), ``skills`` / ``mcp_configs``
reference lists and a ``tables`` map. A legacy layout (``chunks.jsonl``,
``graph/*.json``, ``catalog/catalog.json``, versions 1.0–1.1) is still
accepted on read — see ``BUNDLE_ACCEPTED_VERSIONS``; readers degrade
gracefully.

Manifest schema (fields shown; all beyond format/version/project_id are
optional on read)::

    {
      "format": "r4-kb",
      "version": "1.3",
      "project_id": "my-project",
      "project_name": "My Research Project",
      "created_at": "2026-07-05T12:00:00Z",
      "embedding": {"model": "e5-base-v2", "dimensions": 768, "pending": false},
      "embedding_model": "intfloat/e5-base-v2",
      "embedding_dimension": 768,
      "chunk_text": true,
      "tables": {"nodes": {"count": 318, "file": "nodes.jsonl"}},
      "skills": [{"ref": "…", "version_pin": "…", "scope": "…"}],
      "mcp_configs": [{"ref": "…", "version_pin": "…", "scope": "…"}]
    }
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import tarfile
import zipfile
from array import array
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BUNDLE_FORMAT = "r4-kb"
# Version emitted by the client-side manifest and the CLI projection. Matches
# the server's durable export job (ADR-032 amendment + WP-17 addendum): all
# portable-bundle producers emit v1.2 (v1.3 only with --chunk-text). The
# retired flat-layout writer was removed in WP-18; versions 1.0/1.1 remain
# accepted on read only.
BUNDLE_VERSION = "1.2"
# Readers accept all of these (ADR-032 amendment 2026-09; ADR-093 D-12 adds
# 1.3, the v1.2 layout with chunk text on chunk nodes).
BUNDLE_ACCEPTED_VERSIONS = {"1.0", "1.1", "1.2", "1.3"}
MANIFEST_FILENAME = "manifest.json"

DEFAULT_EMBEDDING_MODEL = "intfloat/e5-base-v2"
DEFAULT_EMBEDDING_DIMENSION = 768

# The umrs export's flat tables the offline reader uses (r4-kb v1.2/v1.3).
NODES_FILENAME = "nodes.jsonl"
NODE_EMBEDDINGS_FILENAME = "node_embeddings.jsonl"
ARTIFACTS_FILENAME = "artifacts.jsonl"
RAW_TABLE_FILENAME = "raw_objects.jsonl"
RAW_DIRNAME = "raw"

#: Where an offline chunk's text came from (``r4_contracts.retrieval.TEXT_SOURCES``):
#: the node's own text (v1.3 ``--chunk-text``, or a canonized legacy chunk),
#: the md5-verified slice of a carried raw object, else the excerpt.
TEXT_FROM_NODE = "node"
TEXT_FROM_ARTIFACT = "artifact"
TEXT_FROM_EXCERPT = "excerpt"

# Decoded raw objects kept while reading nodes.jsonl (an artifact's chunk
# nodes are adjacent there, so a few suffice).
_RAW_CACHE_ARTIFACTS = 8
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


@dataclass
class KnowledgeBundleManifest:
    """Manifest describing a knowledge bundle."""

    format: str = BUNDLE_FORMAT
    version: str = BUNDLE_VERSION
    project_id: str = ""
    project_name: str = ""
    created_at: str = ""
    embedding_model: str = DEFAULT_EMBEDDING_MODEL
    embedding_dimension: int = DEFAULT_EMBEDDING_DIMENSION
    document_count: int = 0
    chunk_count: int = 0
    entity_count: int = 0
    relation_count: int = 0
    catalog_object_count: int = 0
    # v1.2 fields (ADR-032 amendment 2026-09); all optional, absent in legacy
    # flat-layout manifests generated by the legacy writer below this class.
    generator: str = ""
    generator_version: str = ""
    exported_at: str = ""
    unsigned: bool = False
    skills: list = field(default_factory=list)
    mcp_configs: list = field(default_factory=list)
    embedding: dict = field(default_factory=dict)
    tables: dict = field(default_factory=dict)
    # v1.3 (ADR-093 D-12): chunk nodes carry their text.
    chunk_text: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "KnowledgeBundleManifest":
        # Trust pin (v1.2): if present and not pending, it is authoritative
        # for the legacy alias fields instead of the hardcoded defaults.
        embedding = data.get("embedding") or {}
        pin_model = str(embedding.get("model", "")) if isinstance(embedding, dict) else ""
        pin_dims = int(embedding.get("dimensions") or 0) if isinstance(embedding, dict) else 0
        return cls(
            format=data.get("format", BUNDLE_FORMAT),
            version=data.get("version", BUNDLE_VERSION),
            project_id=data.get("project_id", ""),
            project_name=data.get("project_name", ""),
            created_at=data.get("created_at", ""),
            embedding_model=(
                pin_model if pin_model else data.get("embedding_model", DEFAULT_EMBEDDING_MODEL)
            ),
            embedding_dimension=(
                pin_dims
                if pin_dims
                else data.get("embedding_dimension", DEFAULT_EMBEDDING_DIMENSION)
            ),
            document_count=data.get("document_count", 0),
            chunk_count=data.get("chunk_count", 0),
            entity_count=data.get("entity_count", 0),
            relation_count=data.get("relation_count", 0),
            catalog_object_count=data.get("catalog_object_count", 0),
            generator=data.get("generator", ""),
            generator_version=data.get("generator_version", ""),
            exported_at=data.get("exported_at", ""),
            unsigned=bool(data.get("unsigned", False)),
            skills=list(data.get("skills", [])),
            mcp_configs=list(data.get("mcp_configs", [])),
            embedding=dict(embedding),
            tables=dict(data.get("tables", {})),
            chunk_text=bool(data.get("chunk_text", False)),
        )

    @classmethod
    def from_file(cls, path: Path) -> "KnowledgeBundleManifest":
        """Load a manifest from a ``manifest.json`` file."""
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls.from_dict(data)

    @staticmethod
    def now() -> str:
        """Return current UTC time in ISO 8601 format."""
        return datetime.now(timezone.utc).isoformat()

    def pinned_space(self) -> str:
        """The embedding space the manifest names (ADR-083: the model's
        basename): the trust pin unless it is pending, else the legacy
        ``embedding_model`` field of a manifest without a pin."""
        if self.embedding:
            pending = bool(self.embedding.get("pending"))
            return "" if pending else _basename(self.embedding.get("model"))
        return _basename(self.embedding_model)


# ---------------------------------------------------------------------------
# JSONL and row helpers
# ---------------------------------------------------------------------------


def _iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    """Stream a JSONL file row by row (blank lines skipped)."""
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                yield json.loads(line)


def _read_jsonl_rows(path: Path) -> list[dict[str, Any]]:
    """Read a JSONL file into a list of dicts (tolerating blank lines)."""
    return list(_iter_jsonl(path))


def _iter_optional(path: Path) -> Iterator[dict[str, Any]]:
    """Rows of an optional table; none when the file is absent."""
    if path.is_file():
        yield from _iter_jsonl(path)


def _as_dict(value: Any) -> dict[str, Any]:
    """A JSONB column as exported: a dict, or its JSON text."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def _basename(value: Any) -> str:
    """The last path segment (``/models/e5-base-v2`` → ``e5-base-v2``)."""
    return str(value or "").rstrip("/").rsplit("/", 1)[-1]


def _md5(text: str) -> str:
    return hashlib.md5(text.encode("utf-8"), usedforsecurity=False).hexdigest()


def _chunk_span(content: dict[str, Any]) -> tuple[int, int, str] | None:
    """``(start, end, md5)`` when the chunk node records where its text sits."""
    start, end, md5 = (
        content.get("chunk_start"),
        content.get("chunk_end"),
        content.get("chunk_md5"),
    )
    if isinstance(start, int) and isinstance(end, int) and md5:
        return start, end, str(md5)
    return None


def _first_text(*values: Any) -> str:
    """The first non-blank string among ``values`` ("" when none)."""
    for value in values:
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _artifact_label(row: dict[str, Any]) -> str:
    """An artifact's trusted name, in the server's order (``source_name``,
    the staged upload key, the title), then the upload's filename."""
    meta = _as_dict(row.get("metadata"))
    return _first_text(
        meta.get("source_name"),
        _basename(meta.get("staged_from")),
        meta.get("title"),
        row.get("title"),
        meta.get("filename"),
    ).strip()


# ---------------------------------------------------------------------------
# Chunk rows of the flat layout
# ---------------------------------------------------------------------------


@dataclass
class _ArtifactIndex:
    """``artifacts.jsonl`` and ``raw_objects.jsonl`` as the chunk reader
    needs them: labels, the artifact of a rag.upload document, raw objects."""

    root: Path
    labels: dict[str, str] = field(default_factory=dict)
    by_document: dict[str, str] = field(default_factory=dict)
    digests: dict[str, str] = field(default_factory=dict)
    raw_digests: set[str] = field(default_factory=set)

    @classmethod
    def load(cls, root: Path) -> "_ArtifactIndex":
        index = cls(root)
        for row in _iter_optional(root / ARTIFACTS_FILENAME):
            index._add(row)
        for row in _iter_optional(root / RAW_TABLE_FILENAME):
            index.raw_digests.add(str(row.get("content_hash") or ""))
        return index

    def _add(self, row: dict[str, Any]) -> None:
        artifact_id = str(row.get("artifact_id") or "")
        if not artifact_id:
            return
        self.labels[artifact_id] = _artifact_label(row)
        self.digests[artifact_id] = str(row.get("content_hash") or "")
        document_id = _as_dict(row.get("metadata")).get("document_id")
        if document_id:
            self.by_document.setdefault(str(document_id), artifact_id)

    def artifact_of(self, content: dict[str, Any]) -> str:
        """The chunk's artifact: its own ``artifact_id``, else the artifact
        its ``document_id`` names (chunks minted by a rag upload)."""
        artifact_id = str(content.get("artifact_id") or "")
        if artifact_id:
            return artifact_id
        return self.by_document.get(str(content.get("document_id") or ""), "")

    def raw_path(self, artifact_id: str) -> Path | None:
        """The artifact's carried raw object (``raw/<sha256>``), when the raw
        table lists it and the file is present. The path is built from the
        digest, never from a row's ``file`` field."""
        digest = self.digests.get(artifact_id, "")
        if digest not in self.raw_digests or not _HEX64.match(digest):
            return None
        path = self.root / RAW_DIRNAME / digest
        return path if path.is_file() else None


class _RawTexts:
    """Decoded raw objects, a few artifacts at a time, each read once."""

    def __init__(self, index: _ArtifactIndex) -> None:
        self._index = index
        self._cache: dict[str, str | None] = {}

    def text(self, artifact_id: str) -> str | None:
        if artifact_id not in self._cache:
            if len(self._cache) >= _RAW_CACHE_ARTIFACTS:
                self._cache.pop(next(iter(self._cache)))
            self._cache[artifact_id] = self._decode(artifact_id)
        return self._cache[artifact_id]

    def _decode(self, artifact_id: str) -> str | None:
        path = self._index.raw_path(artifact_id)
        if path is None:
            return None
        return decode_chunked(path.read_bytes())


#: A PDF, or a zip container (a PPTX): the embed job chunked the text its
#: extractor produced, which an offline reader cannot reproduce.
_EXTRACTED_MAGIC = (b"%PDF", b"PK\x03\x04")


def decode_chunked(raw: bytes) -> str | None:
    """The text the embed job chunked from a raw object, as umrs
    ``retrieval.hydrate.decode_chunked`` decodes it: UTF-8 with undecodable
    bytes dropped, NUL removed, outer whitespace stripped; chunk offsets index
    this string. ``None`` for a PDF or a PPTX, whose chunks index extracted
    text (their chunks fall back to the node text or the excerpt; review
    chunk-data-12, 2026-10-10)."""
    if raw.startswith(_EXTRACTED_MAGIC):
        return None
    return raw.decode("utf-8", errors="ignore").replace("\x00", "").strip()


def _raw_slice(content: dict[str, Any], texts: _RawTexts, artifact_id: str) -> str:
    """The chunk's text from its artifact's raw object, only when its md5
    matches the node's ``chunk_md5``."""
    span = _chunk_span(content)
    document = texts.text(artifact_id) if span is not None and artifact_id else None
    if span is None or not document:
        return ""
    piece = document[span[0] : span[1]]
    return piece if _md5(piece) == span[2] else ""


def _md5_matches(text: str, content: dict[str, Any]) -> bool:
    span = _chunk_span(content)
    return span is not None and _md5(text) == span[2]


def _chunk_text(
    row: dict[str, Any], content: dict[str, Any], texts: _RawTexts, artifact_id: str
) -> tuple[str, str]:
    """``(text, text_source)`` of a chunk node, the umrs reader's order
    (``bundle_reader.chunk_passage``): the node's own text when its md5
    matches (a v1.3 node's ``text``, else a canonized chunk's
    ``content.text``), the md5-verified raw slice, the node's text cut at
    4,096 characters or unverified, else the excerpt; ``("", "")`` when the
    node has none."""
    own = _first_text(row.get("text"), content.get("text"))
    if own and _md5_matches(own, content):
        return own, TEXT_FROM_NODE
    sliced = _raw_slice(content, texts, artifact_id)
    if sliced:
        return sliced, TEXT_FROM_ARTIFACT
    if own:
        return own, TEXT_FROM_NODE
    excerpt = _first_text(content.get("excerpt"))
    return (excerpt, TEXT_FROM_EXCERPT) if excerpt else ("", "")


def _chunk_position(content: dict[str, Any]) -> tuple[int, list[int] | None, str]:
    """``(chunk_index, [start, end] | None, section)`` of a chunk node."""
    index = content.get("chunk_index")
    span = _chunk_span(content)
    return (
        index if isinstance(index, int) else 0,
        [span[0], span[1]] if span else None,
        str(content.get("section") or ""),
    )


def _chunk_row(
    row: dict[str, Any], content: dict[str, Any], index: _ArtifactIndex, texts: _RawTexts
) -> dict[str, Any]:
    """One chunk node as an offline chunk row (the legacy chunk keys
    ``chunk_id``/``document_id``/``content``/``metadata`` plus the passage
    fields the retrieve verb returns)."""
    node_id = str(row.get("node_id") or "")
    artifact_id = index.artifact_of(content)
    text, text_source = _chunk_text(row, content, texts, artifact_id)
    document_id = artifact_id or str(content.get("document_id") or "")
    name = index.labels.get(artifact_id) or document_id or node_id
    position, span, section = _chunk_position(content)
    return {
        "chunk_id": node_id,
        "node_id": node_id,
        "document_id": document_id,
        "artifact_id": artifact_id,
        "content": text,
        # The C9 header: the exported prefix, else the node's own (a v1.2
        # bundle, or a v1.3 chunk exported through the artifact slice).
        "header": _first_text(row.get("text_header"), content.get("header")),
        "text_source": text_source,
        "source_name": name,
        "section": section,
        "chunk_index": position,
        "chunk_count": 0,
        "span": span,
        "metadata": {
            "node_id": node_id,
            "artifact_id": artifact_id,
            "source_name": name,
            "section": section,
            "chunk_index": position,
            "text_source": text_source,
            "text_truncated": bool(row.get("text_truncated")),
        },
    }


def _with_chunk_counts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fill each row's ``chunk_count`` (chunks of its document)."""
    counts = Counter(row["document_id"] for row in rows)
    for row in rows:
        row["chunk_count"] = counts[row["document_id"]]
    return rows


def _vector_row(row: dict[str, Any]) -> dict[str, Any] | None:
    """A node_embeddings.jsonl row keyed by its node id, with its space."""
    node_id = str(row.get("node_id") or "")
    vector = row.get("embedding")
    if not node_id or not isinstance(vector, list) or not vector:
        return None
    return {**row, "node_id": node_id, "space": _basename(row.get("model_name"))}


def _keyed_vector(row: dict[str, Any], pinned: str) -> tuple[str, str, list[float]] | None:
    """``(space, chunk id, vector)`` of an embedding row: a flat row's node id
    and space, a legacy row's chunk id in the manifest's space."""
    key = str(row.get("node_id") or row.get("chunk_id") or "")
    vector = row.get("embedding")
    if not key or not isinstance(vector, list) or not vector:
        return None
    return str(row.get("space") or pinned), key, vector


def _pick_space(by_space: dict[str, dict[str, array]], pinned: str) -> str:
    """The pinned space when the bundle has chunk vectors in it, else the
    space holding the most chunk vectors."""
    if pinned in by_space:
        return pinned
    if not by_space:
        return ""
    return max(by_space, key=lambda space: len(by_space[space]))


# ---------------------------------------------------------------------------
# Bundle Reader
# ---------------------------------------------------------------------------


def _safe_extract_tar(archive: Path, dest: Path) -> None:
    """Extract a bundle archive: regular files and directories only, no
    absolute or ``..`` paths (a hostile archive raises ValueError)."""
    with tarfile.open(archive, "r:gz") as tf:
        members = tf.getmembers()
        for member in members:
            parts = Path(member.name).parts
            if member.name.startswith("/") or ".." in parts:
                raise ValueError(f"unsafe path in bundle archive: {member.name!r}")
            if not (member.isfile() or member.isdir()):
                raise ValueError(f"unsupported member in bundle archive: {member.name!r}")
        tf.extractall(dest, members=members)  # noqa: S202 — members checked above


def _archive_root(dest: Path) -> Path:
    """The directory holding the manifest: the top level or its single child
    (the server packs the bundle under ``bundle/``)."""
    if (dest / MANIFEST_FILENAME).is_file():
        return dest
    children = [p for p in dest.iterdir() if p.is_dir()]
    if len(children) == 1 and (children[0] / MANIFEST_FILENAME).is_file():
        return children[0]
    return dest


@dataclass
class KnowledgeBundleReader:
    """Read a knowledge bundle from a directory or ``.kb`` zip archive.

    Usage::

        reader = KnowledgeBundleReader(Path("my-project.kb"))
        manifest = reader.read_manifest()
        chunks = reader.read_chunks()
        embeddings = reader.read_embeddings()
        entities, relations = reader.read_graph()
        catalog = reader.read_catalog()
    """

    bundle_path: Path
    _extracted_dir: Path | None = None
    _is_zip: bool = False
    _root: Path | None = None  # manifest directory inside an extracted archive

    def __post_init__(self) -> None:
        self.bundle_path = self.bundle_path.resolve()
        if not self.bundle_path.exists():
            raise FileNotFoundError(f"Knowledge bundle not found: {self.bundle_path}")

        if self.bundle_path.is_file() and self.bundle_path.suffix == ".kb":
            self._is_zip = True
            self._extracted_dir = self._fresh_extract_dir(self.bundle_path.stem)
            with zipfile.ZipFile(self.bundle_path, "r") as zf:
                zf.extractall(self._extracted_dir)
        elif self.bundle_path.is_file() and self.bundle_path.name.endswith(".tar.gz"):
            # The server export archive (D-40): <job>.kb.tar.gz.
            self._is_zip = True
            name = self.bundle_path.name.removesuffix(".tar.gz")
            self._extracted_dir = self._fresh_extract_dir(name)
            _safe_extract_tar(self.bundle_path, self._extracted_dir)
            self._root = _archive_root(self._extracted_dir)
        elif self.bundle_path.is_dir():
            self._extracted_dir = self.bundle_path
        else:
            raise ValueError(
                "Invalid bundle path (must be a .kb file, a .kb.tar.gz archive or "
                f"a directory): {self.bundle_path}"
            )

    def _fresh_extract_dir(self, stem: str) -> Path:
        extracted = self.bundle_path.parent / f".{stem}.extracted"
        if extracted.exists():
            shutil.rmtree(extracted)
        extracted.mkdir(parents=True)
        return extracted

    @property
    def _work_dir(self) -> Path:
        assert self._extracted_dir is not None
        return self._root or self._extracted_dir

    def _flat_layout(self) -> bool:
        """The umrs export layout (v1.2/v1.3): ``nodes.jsonl`` at the root."""
        return (self._work_dir / NODES_FILENAME).is_file()

    def read_manifest(self) -> KnowledgeBundleManifest:
        """Read and return the bundle manifest."""
        manifest_path = self._work_dir / MANIFEST_FILENAME
        if not manifest_path.exists():
            raise FileNotFoundError(f"Manifest not found in bundle: {manifest_path}")
        return KnowledgeBundleManifest.from_file(manifest_path)

    def read_chunks(self) -> list[dict[str, Any]]:
        """Document chunks: the legacy ``chunks.jsonl`` rows as stored, else
        one flat row per chunk node of ``nodes.jsonl`` (v1.2/v1.3).

        A flat row keeps the legacy keys (``chunk_id`` = node id,
        ``document_id`` = artifact id, ``content`` = text, ``metadata``) and
        adds ``node_id``, ``artifact_id``, ``source_name`` (from
        ``artifacts.jsonl``), ``header`` (a C9 chunk's header: the v1.3
        ``text_header``, else ``content.header``; "" without one),
        ``section``, ``chunk_index``, ``chunk_count``, ``span`` and
        ``text_source`` (``node`` | ``artifact`` | ``excerpt``, "" when the
        node has no text). nodes.jsonl is streamed: only chunk rows are kept.
        """
        legacy = self._first_existing(
            self._work_dir / "documents" / "chunks.jsonl",
            self._work_dir / "chunks.jsonl",  # legacy flat layout
        )
        if legacy is not None:
            return _read_jsonl_rows(legacy)
        if not self._flat_layout():
            return []
        return _with_chunk_counts(list(self._iter_flat_chunks()))

    def _iter_flat_chunks(self) -> Iterator[dict[str, Any]]:
        index = _ArtifactIndex.load(self._work_dir)
        texts = _RawTexts(index)
        for row in _iter_jsonl(self._work_dir / NODES_FILENAME):
            content = _as_dict(row.get("content"))
            if content.get("kind") == "chunk":
                yield _chunk_row(row, content, index, texts)

    def _first_existing(self, *paths: Path) -> Path | None:
        """Return the first path that exists, or None."""
        for path in paths:
            if path.exists():
                return path
        return None

    def read_embeddings(self) -> list[dict[str, Any]]:
        """Embedding rows (see :meth:`iter_embeddings`)."""
        return list(self.iter_embeddings())

    def iter_embeddings(self) -> Iterator[dict[str, Any]]:
        """Stream embedding rows: the legacy layout's rows as stored; for the
        flat layout ``node_embeddings.jsonl``, each row keyed by ``node_id``
        and carrying its ``space`` (the model's basename, ADR-083). The flat
        layout's ``embeddings.jsonl`` (the memory_vectors dump) holds no
        chunk vectors and is never read here."""
        canonical = self._work_dir / "embeddings" / "embeddings.jsonl"
        if canonical.exists():
            yield from _iter_jsonl(canonical)
        elif self._flat_layout():
            for raw in _iter_optional(self._work_dir / NODE_EMBEDDINGS_FILENAME):
                row = _vector_row(raw)
                if row is not None:
                    yield row
        else:
            yield from _iter_optional(self._work_dir / "embeddings.jsonl")  # legacy flat

    def chunk_vectors(self, chunk_ids: Iterable[str]) -> tuple[str, dict[str, array]]:
        """``(space, {node_id: vector})`` for the given chunks, in ONE space.

        Rows are keyed by ``node_id`` (legacy rows by ``chunk_id``), never by
        position. The space is the manifest's pin when the bundle has chunk
        vectors in it, else the space most chunk vectors are in. Vectors are
        kept as float32 arrays; the table is streamed.
        """
        wanted = {key for key in chunk_ids if key}
        pinned = self.read_manifest().pinned_space()
        by_space: dict[str, dict[str, array]] = {}
        for row in self.iter_embeddings():
            keyed = _keyed_vector(row, pinned)
            if keyed is not None and keyed[1] in wanted:
                by_space.setdefault(keyed[0], {})[keyed[1]] = array("f", keyed[2])
        space = _pick_space(by_space, pinned)
        return space, by_space.get(space, {})

    def read_graph(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Read knowledge graph rows (canonical, legacy flat, or 8-table layout)."""
        entities_path = self._first_existing(
            self._work_dir / "graph" / "entities.json",
            self._work_dir / "entities.json",  # legacy flat layout
        )
        relations_path = self._first_existing(
            self._work_dir / "graph" / "relations.json",
            self._work_dir / "relations.json",  # legacy flat layout
        )

        entities: list[dict[str, Any]] = []
        if entities_path is not None:
            entities = json.loads(entities_path.read_text(encoding="utf-8"))

        relations: list[dict[str, Any]] = []
        if relations_path is not None:
            relations = json.loads(relations_path.read_text(encoding="utf-8"))

        if not entities and not relations:
            nodes_path = self._work_dir / NODES_FILENAME  # 8-table (umrs) layout
            edges_path = self._work_dir / "edges.jsonl"
            if nodes_path.exists():
                entities = _read_jsonl_rows(nodes_path)
            if edges_path.exists():
                relations = _read_jsonl_rows(edges_path)

        return entities, relations

    def read_catalog(self) -> list[dict[str, Any]]:
        """Read science catalog metadata (canonical, legacy flat, or 8-table)."""
        cat_path = self._first_existing(
            self._work_dir / "catalog" / "catalog.json",
            self._work_dir / "catalog.json",  # legacy flat layout
        )
        if cat_path is not None:
            return json.loads(cat_path.read_text(encoding="utf-8"))
        cat_sql_path = self._work_dir / "catalog.jsonl"  # 8-table (umrs) layout
        if cat_sql_path.exists():
            return _read_jsonl_rows(cat_sql_path)
        return []

    def read_bm25_index(self) -> Any:
        """Read a serialized BM25 index."""
        import pickle

        bm25_path = self._work_dir / "bm25_index" / "bm25.pkl"
        if not bm25_path.exists():
            return None
        with bm25_path.open("rb") as f:
            return pickle.load(f)

    def cleanup(self) -> None:
        """Clean up extracted files (for zip bundles)."""
        if self._is_zip and self._extracted_dir and self._extracted_dir.exists():
            shutil.rmtree(self._extracted_dir)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_bundle(bundle_path: Path) -> KnowledgeBundleManifest:
    """Validate a knowledge bundle and return its manifest.

    Performs comprehensive validation including:
    - Bundle format and schema version
    - Manifest completeness (required fields)
    - Content integrity (chunk count matches actual chunks)
    - Graph consistency (entity/relation count matches)

    Raises:
        FileNotFoundError: If the bundle or manifest doesn't exist.
        ValueError: If the bundle format, schema, or content is invalid.
    """
    reader = KnowledgeBundleReader(bundle_path)

    # 1. Validate bundle path
    if not reader.bundle_path.exists():
        raise FileNotFoundError(f"Knowledge bundle not found: {reader.bundle_path}")

    # 2. Validate manifest format and schema
    manifest = reader.read_manifest()
    _check_manifest_contract(manifest)
    # 3. Validate content integrity (embeddings are counted, not held)
    entities, relations = reader.read_graph()
    actual = {
        "chunks": len(reader.read_chunks()),
        "embeddings": sum(1 for _row in reader.iter_embeddings()),
        "entities": len(entities),
        "relations": len(relations),
        "catalog objects": len(reader.read_catalog()),
    }
    _warn_count_mismatches(manifest, actual)
    reader.cleanup()
    return manifest


def _check_manifest_contract(manifest: KnowledgeBundleManifest) -> None:
    """Format / schema-version / required-field checks (raises ValueError)."""
    if manifest.format != BUNDLE_FORMAT:
        raise ValueError(
            f"Invalid bundle format: expected '{BUNDLE_FORMAT}', got '{manifest.format}'"
        )
    if manifest.version not in BUNDLE_ACCEPTED_VERSIONS:
        raise ValueError(
            f"Unsupported bundle schema version: got '{manifest.version}' "
            f"(accepted: {', '.join(sorted(BUNDLE_ACCEPTED_VERSIONS))}). Upgrade the "
            "knowledge bundle or use a compatible r4 version."
        )
    if not manifest.project_id:
        raise ValueError("Bundle manifest missing required field: 'project_id'")


def _declared_counts(manifest: KnowledgeBundleManifest) -> dict[str, int | None]:
    """The counts the manifest declares. A v1.2+ manifest declares its counts
    in ``tables``, so its unset legacy count fields (0) declare nothing."""
    legacy_only = not manifest.tables
    declared: dict[str, int | None] = {
        "chunks": manifest.chunk_count,
        "embeddings": getattr(manifest, "embedding_count", None),
        "entities": manifest.entity_count,
        "relations": manifest.relation_count,
        "catalog objects": getattr(manifest, "catalog_object_count", None),
    }
    return {name: n for name, n in declared.items() if n is not None and (n or legacy_only)}


def _warn_count_mismatches(manifest: KnowledgeBundleManifest, actual: dict[str, int]) -> None:
    """Warn (not fail) when manifest counts disagree with actual content."""
    for name, declared in _declared_counts(manifest).items():
        if declared != actual.get(name, 0):
            logger.warning(
                "Bundle count mismatch: manifest reports %d %s, found %d",
                declared,
                name,
                actual.get(name, 0),
            )
    if not actual.get("chunks") and not actual.get("entities"):
        logger.warning("Bundle is empty (0 chunks, 0 entities)")


__all__ = [
    "BUNDLE_ACCEPTED_VERSIONS",
    "BUNDLE_FORMAT",
    "BUNDLE_VERSION",
    "TEXT_FROM_ARTIFACT",
    "TEXT_FROM_EXCERPT",
    "TEXT_FROM_NODE",
    "KnowledgeBundleManifest",
    "KnowledgeBundleReader",
    "validate_bundle",
]
