"""Read-only database introspection for coddie-dev.

Hard guarantees:
  * every statement is checked before execution - a single SELECT (or WITH ...
    SELECT) only, no semicolon-chaining, no DML/DDL;
  * the connection is opened read-only where the driver supports it;
  * columns matching database.redact_columns are masked in sampled rows;
  * a row limit is always applied.

Drivers are imported lazily so the toolkit installs with zero DB dependencies.
"""

from __future__ import annotations

import fnmatch
import re
from typing import Any

from .config import Config

FORBIDDEN = re.compile(
    r"\b(insert|update|delete|merge|upsert|drop|alter|create|truncate|grant|revoke|"
    r"commit|rollback|savepoint|call|exec|execute|lock|vacuum|copy|into\s+outfile)\b",
    re.IGNORECASE,
)


class DbError(RuntimeError):
    pass


def assert_read_only(sql: str) -> str:
    stripped = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    stripped = re.sub(r"--[^\n]*", " ", stripped).strip().rstrip(";").strip()
    if not stripped:
        raise DbError("empty statement")
    if ";" in stripped:
        raise DbError("multiple statements are not allowed - send one SELECT")
    first = stripped.split(None, 1)[0].lower()
    if first not in ("select", "with"):
        raise DbError(f"only SELECT is allowed, got '{first.upper()}'")
    hit = FORBIDDEN.search(stripped)
    if hit:
        raise DbError(f"statement contains forbidden keyword '{hit.group(0).upper()}'")
    return stripped


def _should_redact(column: str, patterns: list) -> bool:
    col = (column or "").lower()
    return any(fnmatch.fnmatch(col, str(p).lower()) for p in patterns or [])


class Database:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        section = cfg.section("database")
        if not section:
            raise DbError("no 'database' section in credentials.yaml")
        if not section.get("enabled", False):
            raise DbError("database.enabled is false - set it to true to allow schema scans")
        self.dialect = str(section.get("dialect", "postgres")).lower()
        self.cfgdb = section
        self.redact = section.get("redact_columns", []) or []
        self._conn = None

    # ------------------------------------------------------------------ connection

    def connect(self):
        if self._conn is not None:
            return self._conn
        d = self.cfgdb
        try:
            if self.dialect in ("postgres", "postgresql"):
                import psycopg2  # noqa: PLC0415

                self._conn = psycopg2.connect(
                    host=d.get("host", "localhost"), port=int(d.get("port", 5432)),
                    dbname=d.get("database"), user=d.get("user"), password=d.get("password"),
                )
                self._conn.set_session(readonly=True, autocommit=True)
            elif self.dialect in ("mysql", "mariadb"):
                try:
                    import pymysql as driver  # noqa: PLC0415
                except ImportError:
                    import mysql.connector as driver  # noqa: PLC0415
                self._conn = driver.connect(
                    host=d.get("host", "localhost"), port=int(d.get("port", 3306)),
                    database=d.get("database"), user=d.get("user"), password=d.get("password"),
                )
            elif self.dialect == "oracle":
                import oracledb  # noqa: PLC0415

                dsn = d.get("dsn") or oracledb.makedsn(
                    d.get("host", "localhost"), int(d.get("port", 1521)),
                    service_name=d.get("service_name") or d.get("database"),
                )
                self._conn = oracledb.connect(
                    user=d.get("user"), password=d.get("password"), dsn=dsn
                )
            elif self.dialect in ("sqlserver", "mssql"):
                import pyodbc  # noqa: PLC0415

                driver_name = d.get("odbc_driver", "ODBC Driver 17 for SQL Server")
                self._conn = pyodbc.connect(
                    f"DRIVER={{{driver_name}}};SERVER={d.get('host')},{d.get('port', 1433)};"
                    f"DATABASE={d.get('database')};UID={d.get('user')};PWD={d.get('password')}",
                    readonly=True,
                )
            elif self.dialect == "sqlite":
                import sqlite3  # noqa: PLC0415

                path = d.get("database")
                self._conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            else:
                raise DbError(f"unsupported dialect '{self.dialect}'")
        except ImportError as exc:
            raise DbError(
                f"driver for '{self.dialect}' is not installed - {exc}\n"
                f"  install it, e.g.: pip install "
                + {
                    "postgres": "psycopg2-binary", "postgresql": "psycopg2-binary",
                    "mysql": "pymysql", "mariadb": "pymysql", "oracle": "oracledb",
                    "sqlserver": "pyodbc", "mssql": "pyodbc",
                }.get(self.dialect, self.dialect)
            ) from exc
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None

    # ------------------------------------------------------------------ query

    def query(self, sql: str, params: tuple = (), limit: int = 100) -> tuple:
        sql = assert_read_only(sql)
        conn = self.connect()
        cur = conn.cursor()
        try:
            cur.execute(sql, params)
            columns = [c[0] for c in (cur.description or [])]
            rows = cur.fetchmany(limit) if limit else cur.fetchall()
            return columns, [list(r) for r in rows]
        finally:
            cur.close()

    def query_redacted(self, sql: str, params: tuple = (), limit: int = 100) -> tuple:
        columns, rows = self.query(sql, params, limit)
        masked = [i for i, c in enumerate(columns) if _should_redact(c, self.redact)]
        if masked:
            for row in rows:
                for i in masked:
                    if row[i] is not None:
                        row[i] = "***"
        return columns, rows

    # ------------------------------------------------------------------ introspection

    def _schema(self) -> str:
        return self.cfgdb.get("schema") or {
            "postgres": "public", "postgresql": "public",
        }.get(self.dialect, self.cfgdb.get("database", ""))

    def tables(self, like: str = None) -> tuple:
        schema = self._schema()
        if self.dialect == "sqlite":
            sql = "SELECT name AS table_name, type AS table_type FROM sqlite_master WHERE type IN ('table','view')"
            if like:
                sql += f" AND name LIKE '{_safe_like(like)}'"
            return self.query(sql + " ORDER BY name", limit=500)
        if self.dialect == "oracle":
            sql = (
                "SELECT table_name, 'TABLE' AS table_type FROM all_tables "
                f"WHERE owner = '{_safe_ident(schema).upper()}'"
            )
            if like:
                sql += f" AND UPPER(table_name) LIKE UPPER('{_safe_like(like)}')"
            return self.query(sql + " ORDER BY table_name", limit=500)
        sql = (
            "SELECT table_name, table_type FROM information_schema.tables "
            f"WHERE table_schema = '{_safe_ident(schema)}'"
        )
        if like:
            sql += f" AND table_name LIKE '{_safe_like(like)}'"
        return self.query(sql + " ORDER BY table_name", limit=500)

    def columns(self, table: str) -> tuple:
        schema = self._schema()
        table_s = _safe_ident(table)
        if self.dialect == "sqlite":
            return self.query(f"SELECT * FROM pragma_table_info('{table_s}')", limit=500)
        if self.dialect == "oracle":
            return self.query(
                "SELECT column_name, data_type, data_length, nullable, data_default "
                f"FROM all_tab_columns WHERE owner = '{_safe_ident(schema).upper()}' "
                f"AND table_name = '{table_s.upper()}' ORDER BY column_id",
                limit=500,
            )
        return self.query(
            "SELECT column_name, data_type, character_maximum_length, is_nullable, column_default "
            "FROM information_schema.columns "
            f"WHERE table_schema = '{_safe_ident(schema)}' AND table_name = '{table_s}' "
            "ORDER BY ordinal_position",
            limit=500,
        )

    def keys(self, table: str) -> tuple:
        schema = self._schema()
        table_s = _safe_ident(table)
        if self.dialect == "sqlite":
            return self.query(f"SELECT * FROM pragma_foreign_key_list('{table_s}')", limit=200)
        if self.dialect == "oracle":
            return self.query(
                "SELECT c.constraint_name, c.constraint_type, cc.column_name, "
                "c.r_constraint_name FROM all_constraints c "
                "JOIN all_cons_columns cc ON c.constraint_name = cc.constraint_name "
                f"WHERE c.owner = '{_safe_ident(schema).upper()}' "
                f"AND c.table_name = '{table_s.upper()}' AND c.constraint_type IN ('P','R','U')",
                limit=200,
            )
        return self.query(
            "SELECT tc.constraint_name, tc.constraint_type, kcu.column_name, "
            "ccu.table_name AS references_table, ccu.column_name AS references_column "
            "FROM information_schema.table_constraints tc "
            "JOIN information_schema.key_column_usage kcu "
            "  ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema "
            "LEFT JOIN information_schema.constraint_column_usage ccu "
            "  ON tc.constraint_name = ccu.constraint_name "
            f"WHERE tc.table_schema = '{_safe_ident(schema)}' AND tc.table_name = '{table_s}'",
            limit=200,
        )

    def sample(self, table: str, limit: int = 5) -> tuple:
        schema = self._schema()
        qualified = f"{_safe_ident(schema)}.{_safe_ident(table)}" if schema and self.dialect != "sqlite" else _safe_ident(table)
        if self.dialect == "oracle":
            sql = f"SELECT * FROM {qualified} FETCH FIRST {int(limit)} ROWS ONLY"
        elif self.dialect in ("sqlserver", "mssql"):
            sql = f"SELECT TOP {int(limit)} * FROM {qualified}"
        else:
            sql = f"SELECT * FROM {qualified} LIMIT {int(limit)}"
        return self.query_redacted(sql, limit=limit)

    def row_count(self, table: str) -> int:
        schema = self._schema()
        qualified = f"{_safe_ident(schema)}.{_safe_ident(table)}" if schema and self.dialect != "sqlite" else _safe_ident(table)
        _, rows = self.query(f"SELECT COUNT(*) FROM {qualified}", limit=1)
        return rows[0][0] if rows else 0


def _safe_ident(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$#]*", str(name or "")):
        raise DbError(f"unsafe identifier: {name!r}")
    return str(name)


def _safe_like(pattern: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_%$#.\-]*", str(pattern or "")):
        raise DbError(f"unsafe LIKE pattern: {pattern!r}")
    return str(pattern)


def render_table(columns: list, rows: list, max_width: int = 40) -> str:
    if not columns:
        return "(no columns)"

    def cell(v: Any) -> str:
        text = "" if v is None else str(v)
        text = text.replace("\n", " ").replace("\r", "")
        return text[: max_width - 1] + "…" if len(text) > max_width else text

    header = [str(c) for c in columns]
    body = [[cell(v) for v in row] for row in rows]
    widths = [
        max(len(header[i]), *(len(r[i]) for r in body)) if body else len(header[i])
        for i in range(len(header))
    ]
    sep = "-+-".join("-" * w for w in widths)
    out = [" | ".join(h.ljust(widths[i]) for i, h in enumerate(header)), sep]
    out += [" | ".join(r[i].ljust(widths[i]) for i in range(len(header))) for r in body]
    out.append(f"({len(rows)} row{'s' if len(rows) != 1 else ''})")
    return "\n".join(out)
