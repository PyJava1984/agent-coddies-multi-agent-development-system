# Database scanning

coddie-dev uses this to understand the data model before changing code that
touches it. It is an introspection tool, not a data-access layer.

Enable it in `credentials.yaml → database` (`enabled: true`). Point it at a
**dev or local** database with a **read-only account**. Not production.

## Commands

```bash
python scripts/coddie_cli.py db schema --like "%partner%"
python scripts/coddie_cli.py db table DM_SUPPLIER --count
python scripts/coddie_cli.py db sample DM_SUPPLIER --limit 5
python scripts/coddie_cli.py db query "SELECT status, COUNT(*) FROM dm_supplier GROUP BY status" --limit 50
```

| Command | Returns |
|---|---|
| `db schema` | tables and views, optionally filtered by a LIKE pattern |
| `db table` | columns with types, lengths, nullability, defaults; primary/foreign/unique keys |
| `db sample` | a handful of real rows, with sensitive columns masked |
| `db query` | one `SELECT`, always row-limited |

## The guarantees

1. **SELECT only.** The statement must start with `SELECT` or `WITH`; anything
   containing `INSERT`, `UPDATE`, `DELETE`, `MERGE`, `DROP`, `ALTER`, `CREATE`,
   `TRUNCATE`, `GRANT`, `CALL`, `EXEC`, `COPY`, `INTO OUTFILE` and friends is
   rejected before it reaches the driver.
2. **One statement.** An embedded `;` is refused, so nothing can be chained.
3. **Read-only session** where the driver supports it (Postgres, SQL Server,
   SQLite `mode=ro`).
4. **Always limited.** Every path applies a row cap.
5. **Redaction.** Columns matching `database.redact_columns` (passwords, tokens,
   emails, phone numbers, national ids …) come back as `***` in `db sample` and
   `db query`.
6. **Identifiers validated.** Table and schema names are checked against
   `[A-Za-z_][A-Za-z0-9_$#]*` before interpolation.

These are guard rails against an accident, not a substitute for using a
least-privilege account. Grant `SELECT` and nothing else.

## Dialects and drivers

| `dialect` | Driver to install |
|---|---|
| `postgres` | `pip install psycopg2-binary` |
| `mysql` / `mariadb` | `pip install pymysql` |
| `oracle` | `pip install oracledb` (set `service_name` or `dsn`) |
| `sqlserver` | `pip install pyodbc` (plus the ODBC driver) |
| `sqlite` | built in |

Drivers are imported lazily — the toolkit installs and runs without any of them.

## Using what you find

- Match the existing column naming and type conventions in the schema you are
  extending. In HICX-style schemas, a field's declared type in metadata is not
  necessarily the physical column type — read both before assuming.
- Check nullability and length limits before writing validation; the UI limit
  should not exceed the column.
- Look at foreign keys before adding one — the convention is usually already set.
- A migration that **drops or renames** a column, or narrows a type, is a
  breaking change. Flag it to the orchestrator for the user to approve; never
  slip one into a routine ticket.
- Never paste sampled rows into a Jira comment, an MR description, or a commit.
  They are real data even in dev.
