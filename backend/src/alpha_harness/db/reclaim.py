"""Give the data directory's free space back to the disk, at startup, before anything opens.

Neither database ever shrinks on its own. DuckDB keeps freed blocks for reuse rather than
returning them, so every catalog sync (which deletes and reinserts each market, then
rewrites ``data_field`` whole to compact it) leaves the file at its high-water mark. SQLite
keeps deleted pages on a freelist the same way, and nothing here ever ran ``VACUUM``. On a
small volume, such as Railway's 0.5 GB on its Free and Trial plans, that slack is what fills
it.

Each file is rewritten into a scratch directory off the volume (``/tmp``, the container's own
disk), checked, then swapped in. A file is only touched when at least :data:`MIN_SLACK` and
:data:`MIN_SHARE` of it is slack, so an ordinary restart costs a size check and nothing more.
Any failure leaves the original exactly as it was and is logged, never raised: a full disk
should not also stop the app from starting.
"""

import contextlib
import os
import shutil
import sqlite3
import tempfile
import time
from contextlib import closing
from pathlib import Path

import duckdb
import structlog

log = structlog.get_logger(__name__)

#: Slack worth a rewrite: at least this many bytes, and this share of the file.
MIN_SLACK = 32 * 1024 * 1024
MIN_SHARE = 0.2
#: Set to ``0`` to skip reclaiming altogether.
ENABLED = "ALPHA_HARNESS_RECLAIM"
#: Where the rewrite happens; must not be on the volume being reclaimed.
SCRATCH = "ALPHA_HARNESS_SCRATCH_DIR"

_MB = 1024 * 1024


def _mb(size: int) -> float:
    return round(size / _MB, 1)


def usage(data_dir: Path) -> dict[str, float]:
    """Each file in the data directory and its backups, in MB, largest first."""
    found = {
        str(p.relative_to(data_dir)): _mb(p.stat().st_size)
        for p in data_dir.rglob("*")
        if p.is_file()
    }
    return dict(sorted(found.items(), key=lambda kv: kv[1], reverse=True))


def _worth_it(size: int, used: int) -> bool:
    slack = size - used
    return slack >= MIN_SLACK and slack >= size * MIN_SHARE


def _scratch() -> Path:
    base = os.environ.get(SCRATCH) or None
    return Path(tempfile.mkdtemp(prefix="alpha-harness-reclaim-", dir=base))


def _swap(fresh: Path, target: Path, sidecars: tuple[Path, ...]) -> None:
    """Put ``fresh`` where ``target`` is.

    With room for both on the volume, it is copied beside the target and renamed over it, so
    a crash leaves one whole file or the other. Without room, the old file has to go first:
    the one moment a crash could lose it, which is logged so it is never a mystery.
    """
    free = shutil.disk_usage(target.parent).free
    staged = target.with_name(f"{target.name}.reclaimed")
    if free > fresh.stat().st_size + 8 * _MB:
        shutil.copyfile(fresh, staged)
        staged.replace(target)
    else:
        log.warning("db.reclaim.tight_swap", path=str(target), free_mb=_mb(free))
        target.unlink()
        shutil.copyfile(fresh, staged)
        staged.replace(target)
    for sidecar in sidecars:
        sidecar.unlink(missing_ok=True)


# --- DuckDB ---------------------------------------------------------------------------


def _duck_tables(conn: duckdb.DuckDBPyConnection, database: str) -> dict[str, int]:
    names = conn.execute(
        "SELECT schema_name, table_name FROM duckdb_tables() WHERE database_name = ? "
        "AND NOT internal",
        [database],
    ).fetchall()
    counts: dict[str, int] = {}
    for schema, table in names:
        row = conn.execute(f'SELECT count(*) FROM "{database}"."{schema}"."{table}"').fetchone()  # noqa: S608
        counts[f"{schema}.{table}"] = int(row[0]) if row else 0
    return counts


def _duck_used(conn: duckdb.DuckDBPyConnection) -> int:
    row = conn.execute("PRAGMA database_size").fetchone()
    if row is None:
        return 0
    sized = dict(zip([d[0] for d in conn.description or ()], row, strict=False))
    return int(sized.get("used_blocks") or 0) * int(sized.get("block_size") or 0)


def reclaim_duckdb(path: Path) -> int:
    """Rewrite the catalog without its slack. Bytes given back; ``0`` when left alone.

    The full-text search index is left behind on purpose: it needs an extension to copy, and
    startup rebuilds it in the background whenever it is missing.
    """
    if not path.exists():
        return 0
    wal = path.with_name(f"{path.name}.wal")
    with closing(duckdb.connect(str(path))) as conn:
        conn.execute("CHECKPOINT")
        size, used = path.stat().st_size, _duck_used(conn)
        if not _worth_it(size, used):
            return 0
        work = _scratch()
        try:
            fresh = work / path.name
            conn.execute(f"ATTACH '{fresh}' AS fresh")
            source = conn.execute("SELECT current_database()").fetchone()
            name = str(source[0]) if source else "main"
            for (schema,) in conn.execute(
                "SELECT DISTINCT schema_name FROM duckdb_tables() WHERE database_name = ? "
                "AND NOT internal AND schema_name NOT LIKE 'fts_%'",
                [name],
            ).fetchall():
                conn.execute(f'CREATE SCHEMA IF NOT EXISTS fresh."{schema}"')
            # Tables one by one, with their keys and NOT NULLs, then their data; the search
            # index's own schema is skipped (see above).
            tables = conn.execute(
                "SELECT schema_name, table_name, sql FROM duckdb_tables() WHERE database_name = ? "
                "AND NOT internal AND schema_name NOT LIKE 'fts_%'",
                [name],
            ).fetchall()
            for schema, table, sql in tables:
                conn.execute("USE fresh")
                conn.execute(f'SET schema = "{schema}"')
                conn.execute(sql)
                conn.execute(f'USE "{name}"')
                into, out = f'fresh."{schema}"."{table}"', f'"{name}"."{schema}"."{table}"'
                conn.execute(f"INSERT INTO {into} SELECT * FROM {out}")  # noqa: S608
            for (sql,) in conn.execute(
                "SELECT sql FROM duckdb_indexes() WHERE database_name = ? AND sql IS NOT NULL "
                "AND schema_name NOT LIKE 'fts_%'",
                [name],
            ).fetchall():
                conn.execute("USE fresh")
                conn.execute(sql)
                conn.execute(f'USE "{name}"')
            before = {k: v for k, v in _duck_tables(conn, name).items() if not k.startswith("fts_")}
            after = _duck_tables(conn, "fresh")
            conn.execute("DETACH fresh")
            if before != after:
                log.warning("db.reclaim.duckdb_mismatch", before=before, after=after)
                return 0
            conn.close()
            _swap(fresh, path, (wal,))
            return size - fresh.stat().st_size
        finally:
            shutil.rmtree(work, ignore_errors=True)


# --- SQLite ---------------------------------------------------------------------------


def reclaim_sqlite(path: Path) -> int:
    """``VACUUM INTO`` scratch, then swap. Bytes given back; ``0`` when left alone."""
    if not path.exists():
        return 0
    sidecars = (path.with_name(f"{path.name}-wal"), path.with_name(f"{path.name}-shm"))
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        page = int(conn.execute("PRAGMA page_size").fetchone()[0])
        free = int(conn.execute("PRAGMA freelist_count").fetchone()[0]) * page
        size = path.stat().st_size
        if not _worth_it(size, size - free):
            return 0
        if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            log.warning("db.reclaim.sqlite_not_ok", path=str(path))
            return 0
        work = _scratch()
        try:
            fresh = work / path.name
            conn.execute("VACUUM INTO ?", [str(fresh)])
            with closing(sqlite3.connect(fresh)) as check:
                ok = check.execute("PRAGMA quick_check").fetchone()[0] == "ok"
            if not ok:
                log.warning("db.reclaim.sqlite_copy_not_ok", path=str(path))
                return 0
            conn.close()
            _swap(fresh, path, sidecars)
            return size - fresh.stat().st_size
        finally:
            shutil.rmtree(work, ignore_errors=True)


def reclaim(data_dir: Path, sqlite_path: Path, duckdb_path: Path) -> None:
    """Report what the data directory holds, then reclaim both databases' slack."""
    if os.environ.get(ENABLED, "1") == "0":
        return
    with contextlib.suppress(OSError):
        total = shutil.disk_usage(data_dir)
        log.info(
            "db.storage",
            files_mb=usage(data_dir),
            volume_used_mb=_mb(total.used),
            volume_free_mb=_mb(total.free),
        )
    for label, path, work in (
        ("sqlite", sqlite_path, reclaim_sqlite),
        ("duckdb", duckdb_path, reclaim_duckdb),
    ):
        started = time.monotonic()
        try:
            freed = work(path)
        except Exception:
            log.warning("db.reclaim.failed", database=label, exc_info=True)
            continue
        if freed:
            log.info(
                "db.reclaimed",
                database=label,
                freed_mb=_mb(freed),
                seconds=round(time.monotonic() - started, 1),
            )
