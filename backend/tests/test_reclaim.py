# ruff: noqa: S101  -- a test file asserts
"""Startup reclaim: both databases come back smaller with exactly the same contents."""

import asyncio
import sqlite3
from collections import namedtuple
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import duckdb
import pytest

from alpha_harness.db import reclaim as reclaim_module
from alpha_harness.db.duck import FIELD_COLUMNS, Catalog

if TYPE_CHECKING:
    from pathlib import Path


def _rows(region: str, count: int) -> list[tuple[object, ...]]:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    return [
        tuple(
            {
                "field_id": f"f{i}",
                "description": f"{i * 7919 % 104729}" * 6,
                "instrument_type": "EQUITY",
                "region": region,
                "delay": 1,
                "universe": "TOP3000",
                "synced_at": now,
                "user_count": i,
            }.get(column)
            for column in FIELD_COLUMNS
        )
        for i in range(count)
    ]


async def _bloated_catalog(path: Path) -> None:
    catalog = Catalog(path)
    await catalog.open()
    for _ in range(3):
        for region in ("USA", "EUR"):
            await catalog.replace_fields(["EQUITY", region, 1, "TOP3000"], _rows(region, 20000))
        await catalog.compact_fields()
    await catalog.execute(
        "INSERT INTO alpha_pnl (alpha_id, date, pnl) SELECT 'a' || (i // 2500), "
        "DATE '2015-01-01' + CAST(i % 2500 AS INTEGER), i FROM range(10000) t(i)"
    )
    await catalog.execute("DELETE FROM data_field WHERE region = 'EUR'")
    await catalog.close()


def _bloated_sqlite(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, b TEXT NOT NULL)")
        conn.executemany("INSERT INTO t (b) VALUES (?)", [("y" * 1000,) for _ in range(20000)])
        conn.commit()
        conn.execute("DELETE FROM t WHERE id % 4 != 0")


def _contents(catalog: Path, database: Path) -> tuple[object, ...]:
    with duckdb.connect(str(catalog)) as conn:
        tables = {
            name: conn.execute(f"SELECT count(*), sum(hash(x)) FROM {name} x").fetchone()  # noqa: S608
            for (name,) in conn.execute(
                "SELECT table_name FROM duckdb_tables() WHERE schema_name = 'main'"
            ).fetchall()
        }
        indexes = sorted(conn.execute("SELECT index_name FROM duckdb_indexes()").fetchall())
        constraints = sorted(
            str(r)
            for r in conn.execute(
                "SELECT table_name, constraint_type, constraint_column_names "
                "FROM duckdb_constraints()"
            ).fetchall()
        )
    with sqlite3.connect(database) as conn:
        rows = conn.execute("SELECT count(*), sum(length(b)) FROM t").fetchone()
    return tables, indexes, constraints, rows


@pytest.mark.parametrize("room", [True, False])
def test_reclaim_keeps_contents_and_frees_space(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, room: bool
) -> None:
    catalog, database = tmp_path / "catalog.duckdb", tmp_path / "harness.db"
    asyncio.run(_bloated_catalog(catalog))
    _bloated_sqlite(database)
    monkeypatch.setattr(reclaim_module, "MIN_SLACK", 1)
    monkeypatch.setattr(reclaim_module, "MIN_SHARE", 0.0)
    monkeypatch.setenv(reclaim_module.SCRATCH, str(tmp_path))
    if not room:
        usage = namedtuple("usage", "total used free")
        monkeypatch.setattr(reclaim_module.shutil, "disk_usage", lambda _: usage(1, 1, 0))
    before = _contents(catalog, database)
    sizes = catalog.stat().st_size, database.stat().st_size

    reclaim_module.reclaim(tmp_path, database, catalog)

    assert _contents(catalog, database) == before
    assert catalog.stat().st_size < sizes[0]
    assert database.stat().st_size < sizes[1]
    assert not list(tmp_path.glob("alpha-harness-reclaim-*"))
    assert not list(tmp_path.glob("*.reclaimed"))


def test_reclaim_leaves_tight_files_alone(tmp_path: Path) -> None:
    catalog, database = tmp_path / "catalog.duckdb", tmp_path / "harness.db"
    asyncio.run(_bloated_catalog(catalog))
    _bloated_sqlite(database)
    reclaim_module.reclaim(tmp_path, database, catalog)
    stamps = catalog.stat().st_mtime_ns, database.stat().st_mtime_ns
    reclaim_module.reclaim(tmp_path, database, catalog)
    assert (catalog.stat().st_mtime_ns, database.stat().st_mtime_ns) == stamps


def test_reclaim_can_be_switched_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database = tmp_path / "harness.db"
    _bloated_sqlite(database)
    monkeypatch.setattr(reclaim_module, "MIN_SLACK", 1)
    monkeypatch.setattr(reclaim_module, "MIN_SHARE", 0.0)
    monkeypatch.setenv(reclaim_module.ENABLED, "0")
    size = database.stat().st_size
    reclaim_module.reclaim(tmp_path, database, tmp_path / "catalog.duckdb")
    assert database.stat().st_size == size
