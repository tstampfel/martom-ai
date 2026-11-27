"""Toni's fence know-how: read the Markdown chunks in `knowledge/` and load them into Postgres.

Each chunk is one Markdown file with YAML front matter (`id`, `title`, `topic`, `sources`,
`status`) followed by the text the advisor answers from. `martom load-knowledge` upserts every
chunk into `knowledge_chunk`, keyed by its `id`, and deletes rows whose file is gone, so the
table always mirrors the folder. Embeddings for these rows arrive in M3-3.
"""

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

import psycopg
import yaml

ChunkStatus = Literal["draft", "approved"]
STATUSES: tuple[str, ...] = get_args(ChunkStatus)  # ("draft", "approved")

CHUNK_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

# Files in the folder that are not chunks (the README is the review note for Toni).
NOT_CHUNKS = {"README.md"}

# `---` on its own line, the YAML, `---` on its own line, then the body.
FRONT_MATTER = re.compile(r"\A---\n(?P<yaml>.*?)\n---\n(?P<body>.*)\Z", re.DOTALL)


class KnowledgeError(Exception):
    """A chunk file is invalid, or the folder holds no usable chunks."""


@dataclass(frozen=True)
class KnowledgeChunk:
    id: str
    title: str
    topic: str
    sources: tuple[str, ...]
    status: ChunkStatus
    body: str  # the Markdown after the front matter, stripped
    source_path: str  # the file name inside the folder, e.g. "01-panelna-ili-rola.md"
    content_hash: str  # SHA-256 of the whole file, so a status change counts as a change


def _required_text(meta: dict[str, object], field: str, name: str) -> str:
    value = meta.get(field)
    if not isinstance(value, str) or not value.strip():
        raise KnowledgeError(f"{name}: front matter field {field!r} must be a non-empty string")
    return value.strip()


def parse_chunk(path: Path) -> KnowledgeChunk:
    """Read one chunk file, validating its front matter. Raises KnowledgeError."""
    name = path.name
    # Normalise Windows line endings so the front matter regex matches either way.
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    match = FRONT_MATTER.match(text)
    if match is None:
        raise KnowledgeError(f"{name}: expected YAML front matter between two '---' lines")

    try:
        # safe_load builds only plain dicts, lists and scalars, never Python objects.
        meta = yaml.safe_load(match.group("yaml"))
    except yaml.YAMLError as exc:
        raise KnowledgeError(f"{name}: invalid YAML front matter: {exc}") from exc
    if not isinstance(meta, dict):
        raise KnowledgeError(f"{name}: front matter must be a mapping of fields")

    chunk_id = _required_text(meta, "id", name)
    if not CHUNK_ID.match(chunk_id):
        raise KnowledgeError(f"{name}: id {chunk_id!r} must be a lowercase slug like 'a-b-c'")
    title = _required_text(meta, "title", name)
    topic = _required_text(meta, "topic", name)
    status = _required_text(meta, "status", name)
    if status not in STATUSES:
        raise KnowledgeError(f"{name}: status {status!r} must be one of {', '.join(STATUSES)}")

    sources = meta.get("sources")
    if not isinstance(sources, list) or not all(
        isinstance(source, str) and source.strip() for source in sources
    ):
        raise KnowledgeError(f"{name}: front matter field 'sources' must be a list of strings")

    body = match.group("body").strip()
    if not body:
        raise KnowledgeError(f"{name}: the chunk text after the front matter is empty")

    return KnowledgeChunk(
        id=chunk_id,
        title=title,
        topic=topic,
        sources=tuple(source.strip() for source in sources),
        status=status,  # type: ignore[arg-type]  # checked against STATUSES above
        body=body,
        source_path=name,
        content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )


def read_chunks(directory: Path) -> list[KnowledgeChunk]:
    """Read every chunk in `directory`, sorted by file name. Raises KnowledgeError."""
    if not directory.is_dir():
        raise KnowledgeError(
            f"knowledge directory not found: {directory} (run from the repo root or pass --dir)"
        )

    chunks: list[KnowledgeChunk] = []
    seen: dict[str, str] = {}  # chunk id -> file name
    for path in sorted(directory.glob("*.md")):
        if path.name in NOT_CHUNKS:
            continue
        chunk = parse_chunk(path)
        if chunk.id in seen:
            raise KnowledgeError(
                f"two chunks share id {chunk.id!r}: {seen[chunk.id]} and {chunk.source_path}"
            )
        seen[chunk.id] = chunk.source_path
        chunks.append(chunk)

    if not chunks:
        raise KnowledgeError(f"no knowledge chunks (*.md with front matter) in {directory}")
    return chunks


@dataclass(frozen=True)
class LoadStats:
    inserted: int
    updated: int
    unchanged: int
    deleted: int


# Insert a new chunk, or overwrite an existing one only when its file changed. The WHERE on
# DO UPDATE skips unchanged rows, so their updated_at stays put. `xmax = 0` is a Postgres
# trick: it is true for a freshly inserted row and false for an updated one. No row comes
# back when the WHERE skipped the update.
UPSERT_CHUNK = """
INSERT INTO knowledge_chunk
    (id, title, topic, sources, status, body, source_path, content_hash)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (id) DO UPDATE SET
    title = EXCLUDED.title,
    topic = EXCLUDED.topic,
    sources = EXCLUDED.sources,
    status = EXCLUDED.status,
    body = EXCLUDED.body,
    source_path = EXCLUDED.source_path,
    content_hash = EXCLUDED.content_hash,
    updated_at = now()
WHERE knowledge_chunk.content_hash IS DISTINCT FROM EXCLUDED.content_hash
RETURNING (xmax = 0) AS inserted
"""


def load_chunks(conn: psycopg.Connection, chunks: list[KnowledgeChunk]) -> LoadStats:
    """Make `knowledge_chunk` match `chunks` exactly, in one transaction."""
    inserted = updated = unchanged = 0
    with conn.transaction():
        for chunk in chunks:
            row = conn.execute(
                UPSERT_CHUNK,
                (
                    chunk.id,
                    chunk.title,
                    chunk.topic,
                    list(chunk.sources),  # psycopg sends a Python list as a Postgres array
                    chunk.status,
                    chunk.body,
                    chunk.source_path,
                    chunk.content_hash,
                ),
            ).fetchone()
            if row is None:
                unchanged += 1
            elif row[0]:
                inserted += 1
            else:
                updated += 1
        # `<> ALL(array)` is SQL for "not in this list".
        deleted = conn.execute(
            "DELETE FROM knowledge_chunk WHERE id <> ALL(%s)", ([chunk.id for chunk in chunks],)
        ).rowcount
    return LoadStats(inserted=inserted, updated=updated, unchanged=unchanged, deleted=deleted)


def run_load_knowledge(database_url: str, directory: Path) -> LoadStats:
    """Read the chunks in `directory` (all valid, or nothing is loaded) and load them."""
    chunks = read_chunks(directory)
    with psycopg.connect(database_url, autocommit=True) as conn:
        return load_chunks(conn, chunks)
