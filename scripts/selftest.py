#!/usr/bin/env python3
"""Offline self-test for the agent-coddies toolkit.

Exercises everything that does not need a live Jira, GitLab, or database:
config loading and redaction, the YAML fallback reader, the markdown -> ADF and
-> wiki converters, the read-only SQL guard, the run-ledger state machine, the
secret scanner, and the test-output parsers.

    python scripts/selftest.py

Exit code 0 means the toolkit is sound on this machine.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from coddie import db, secrets, state, yamlmin  # noqa: E402
from coddie.config import Config, YAML_BACKEND  # noqa: E402
from coddie.jira import adf_to_text, issue_to_markdown, markdown_to_adf, markdown_to_wiki  # noqa: E402

PASSED = 0
FAILED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"  ok    {name}")
    else:
        FAILED += 1
        print(f"  FAIL  {name}" + (f"\n        {detail}" if detail else ""))


def section(title: str) -> None:
    print(f"\n{title}")


# --------------------------------------------------------------------------- yaml


def test_yaml() -> None:
    section(f"yaml reader (backend in use: {YAML_BACKEND}; testing the builtin fallback)")
    text = """
# comment
jira:
  base_url: https://example.atlassian.net
  deployment: cloud
  verify_ssl: true
  retries: 3
  fields:
    - summary
    - status
gitlab:
  protected_branches: [main, master, develop]
  token: ${SOME_TOKEN}
git:
  template: |
    line one
    line two
"""
    data = yamlmin.loads(text)
    check("nested mapping", data["jira"]["base_url"] == "https://example.atlassian.net")
    check("booleans", data["jira"]["verify_ssl"] is True)
    check("integers", data["jira"]["retries"] == 3)
    check("block sequence", data["jira"]["fields"] == ["summary", "status"])
    check("flow sequence", data["gitlab"]["protected_branches"] == ["main", "master", "develop"])
    check("env placeholder kept verbatim", data["gitlab"]["token"] == "${SOME_TOKEN}")
    check("block scalar", data["git"]["template"].strip().splitlines() == ["line one", "line two"])


# --------------------------------------------------------------------------- config


def test_config() -> None:
    section("config loading, env expansion and redaction")
    with tempfile.TemporaryDirectory() as tmp:
        cdir = Path(tmp)
        (cdir / "config.yaml").write_text(
            "workflow:\n  max_fix_iterations: 2\ngitlab:\n  default_target_branch: main\n",
            encoding="utf-8",
        )
        (cdir / "credentials.yaml").write_text(
            "jira:\n"
            "  base_url: https://example.atlassian.net\n"
            "  token: ${CODDIE_TEST_TOKEN}\n"
            "  email: dev@example.com\n"
            "gitlab:\n"
            "  token: glpat-abcdefghijklmnopqrst\n",
            encoding="utf-8",
        )
        os.environ["CODDIE_TEST_TOKEN"] = "super-secret-token-value"
        os.environ["CODDIES_CONFIG_DIR"] = str(cdir)
        from coddie.config import load  # imported late so the env var applies

        cfg = load(strict=True, reload=True)

        check("merges config + credentials", cfg.get("workflow.max_fix_iterations") == 2)
        check("expands ${ENV}", cfg.get("jira.token") == "super-secret-token-value")
        check("dotted lookup with default", cfg.get("nope.missing", "fallback") == "fallback")
        red = cfg.redacted()
        check("redacts token in output", "super-secret-token-value" not in json.dumps(red))
        check("keeps non-secrets readable", red["jira"]["email"] == "dev@example.com")
        scrubbed = cfg.scrub("the token is super-secret-token-value here")
        check("scrubs secrets from outbound text", "super-secret-token-value" not in scrubbed)
        check("scrub leaves the rest", scrubbed.startswith("the token is"))

        missing = []
        try:
            cfg.require("jira.nonexistent")
        except Exception as exc:  # noqa: BLE001
            missing.append(str(exc))
        check("require() fails loudly on a missing key", bool(missing))

        del os.environ["CODDIES_CONFIG_DIR"]
        del os.environ["CODDIE_TEST_TOKEN"]
        load(strict=False, reload=True)

    check("mask hides the middle", Config.mask("abcdefghijklmnop").startswith("abc"))
    check("mask of empty is explicit", Config.mask("") == "(unset)")


# --------------------------------------------------------------------------- jira conversions


def test_jira_markdown() -> None:
    section("jira markdown conversion")
    md = (
        "# Delivered\n\n"
        "Implemented **supplier escaping** with a `bound parameter`.\n\n"
        "- AC1 verified\n"
        "- AC2 verified\n\n"
        "See [MR !482](https://gitlab.example.com/x/-/merge_requests/482).\n\n"
        "```sql\nSELECT 1 FROM dual\n```\n"
    )
    adf = markdown_to_adf(md)
    types = [node["type"] for node in adf["content"]]
    check("produces a doc", adf["type"] == "doc" and adf["version"] == 1)
    check("heading first", types[0] == "heading")
    check("bullet list present", "bulletList" in types)
    check("code block present", "codeBlock" in types)
    flat = json.dumps(adf)
    check("bold mark", '"strong"' in flat)
    check("inline code mark", '"code"' in flat)
    check("link mark with href", '"link"' in flat and "merge_requests/482" in flat)

    round_trip = adf_to_text(adf)
    check("round-trips the heading", "Delivered" in round_trip)
    check("round-trips the list", "AC1 verified" in round_trip)

    wiki = markdown_to_wiki(md)
    check("wiki heading", "h1. Delivered" in wiki)
    check("wiki bold", "*supplier escaping*" in wiki)
    check("wiki code block", "{code:sql}" in wiki)
    check("wiki link", "[MR !482|https://gitlab.example.com" in wiki)

    issue = {
        "key": "HM-1234",
        "fields": {
            "summary": "Allow apostrophes",
            "status": {"name": "In Progress"},
            "issuetype": {"name": "Bug"},
            "labels": ["data", "supplier"],
            "description": {
                "type": "doc", "version": 1,
                "content": [{"type": "paragraph",
                             "content": [{"type": "text", "text": "Names break on '"}]}],
            },
        },
    }
    rendered = issue_to_markdown(issue, "https://example.atlassian.net")
    check("renders the issue heading", rendered.startswith("# HM-1234 - Allow apostrophes"))
    check("renders the browse link", "/browse/HM-1234" in rendered)
    check("renders list fields", "data, supplier" in rendered)
    check("renders the description", "Names break on" in rendered)


# --------------------------------------------------------------------------- sql guard


def test_sql_guard() -> None:
    section("read-only SQL guard")
    allowed = [
        "SELECT * FROM dm_supplier",
        "select id, name from t where name like '%x%'",
        "WITH recent AS (SELECT 1) SELECT * FROM recent",
        "SELECT * FROM t;  ",
    ]
    for sql in allowed:
        try:
            db.assert_read_only(sql)
            check(f"allows: {sql[:40]}", True)
        except db.DbError as exc:
            check(f"allows: {sql[:40]}", False, str(exc))

    blocked = [
        "DELETE FROM dm_supplier",
        "UPDATE t SET x = 1",
        "INSERT INTO t VALUES (1)",
        "DROP TABLE t",
        "TRUNCATE TABLE t",
        "SELECT 1; DROP TABLE t",
        "SELECT * INTO OUTFILE '/tmp/x' FROM t",
        "CALL some_proc()",
        "GRANT ALL ON t TO public",
        "",
    ]
    for sql in blocked:
        try:
            db.assert_read_only(sql)
            check(f"blocks: {sql[:40] or '(empty)'}", False, "it was allowed")
        except db.DbError:
            check(f"blocks: {sql[:40] or '(empty)'}", True)

    for bad in ("t; DROP TABLE x", "t--", "t x"):
        try:
            db._safe_ident(bad)
            check(f"rejects identifier {bad!r}", False, "it was accepted")
        except db.DbError:
            check(f"rejects identifier {bad!r}", True)

    cols, rows = ["id", "name"], [[1, "Acme"], [2, None]]
    table = db.render_table(cols, rows)
    check("renders a table", "id" in table and "Acme" in table and "(2 rows)" in table)


# --------------------------------------------------------------------------- state machine


def test_state() -> None:
    section("run ledger state machine")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        st = state.RunState.open("hm-1234", root, create=True)
        st.init("Allow apostrophes", "HM-1234-apostrophes", "scope text")
        st.save()
        check("uppercases the ticket", st.ticket == "HM-1234")
        check("starts at round 1 in dev", st.data["round"] == 1 and st.data["stage"] == "dev")
        check("writes state.json", (root / "HM-1234" / "state.json").exists())

        st.handoff("qa", "built the thing", "coddie-dev")
        st.save()
        check("handoff to qa does not bump the round", st.data["round"] == 1)

        fid = st.add_finding(severity="high", title="500 on apostrophe", where="x.ts:8",
                             repro="npx playwright test -g apostrophe",
                             expected="201", actual="500")
        st.save()
        check("assigns a finding id", fid == "F001")
        check("a finding sets FAIL", st.data["verdict"] == "FAIL")

        try:
            st.set_verdict(True)
            check("refuses PASS with an open high finding", False, "it was allowed")
        except state.StateError:
            check("refuses PASS with an open high finding", True)

        st.handoff("dev", "1 finding")
        st.save()
        check("qa -> dev bumps the round", st.data["round"] == 2)

        st.resolve_finding("f001", "bound the parameter")
        st.save()
        check("resolves case-insensitively", st.data["findings"][0]["status"] == "fixed")
        check("no findings left open", st.open_findings() == [])

        st.record_tests(runner="playwright", passed=9, failed=0, skipped=1)
        st.handoff("qa", "fixed")
        st.set_verdict(True, "all green")
        st.save()
        check("PASS once findings are closed", st.data["verdict"] == "PASS")
        check("PASS moves the stage to ship", st.data["stage"] == "ship")

        st.ship("https://gitlab.example.com/x/-/merge_requests/482", "482", "abc123", "In Review")
        st.save()
        check("records delivery", st.data["delivery"]["mr_iid"] == "482")
        check("ends at done", st.data["stage"] == "done")

        events = (root / "HM-1234" / "events.jsonl").read_text(encoding="utf-8").strip().splitlines()
        check("appends an audit event per change", len(events) >= 8)
        check("events are valid json", all(json.loads(line)["kind"] for line in events))

        reopened = state.RunState.open("HM-1234", root)
        check("reloads from disk", reopened.data["delivery"]["commit"] == "abc123")
        check("summary renders", "HM-1234" in reopened.summary_text())
        check("findings markdown renders", "F001" in reopened.findings_markdown(only_open=False))

        try:
            state.RunState.open("HM-9999", root)
            check("errors on a missing ledger", False, "it was opened")
        except state.StateError:
            check("errors on a missing ledger", True)

    check("slugify", state.slugify("Allow apostrophes in Supplier Names!") == "allow-apostrophes-in-supplier-names")
    check("slugify truncates", len(state.slugify("x" * 100)) <= 40)


# --------------------------------------------------------------------------- secrets


def test_secrets() -> None:
    section("secret scanner")
    patterns = [
        r"glpat-[A-Za-z0-9_\-]{20,}",
        r"(?i)(password|passwd|pwd)\s*[:=]\s*['\"][^'\"]{6,}",
        r"-----BEGIN (RSA )?PRIVATE KEY-----",
    ]
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        clean = tmpdir / "clean.py"
        clean.write_text("token = os.environ['GITLAB_TOKEN']\n", encoding="utf-8")
        dirty = tmpdir / "dirty.py"
        dirty.write_text(
            'GITLAB = "glpat-abcdefghij1234567890xy"\npassword = "hunter2000"\n', encoding="utf-8"
        )
        env = tmpdir / ".env"
        env.write_text("NOTHING=1\n", encoding="utf-8")

        hits, forbidden = secrets.scan_paths([str(clean)], patterns, [])
        check("clean file produces no hits", hits == [])

        hits, forbidden = secrets.scan_paths([str(dirty)], patterns, [])
        check("detects a GitLab PAT", any("glpat" in h.pattern for h in hits))
        check("detects a password assignment", len(hits) == 2)
        check("masks the value in its own output",
              all("abcdefghij1234567890xy" not in h.excerpt for h in hits),
              "; ".join(h.excerpt for h in hits))

        hits, forbidden = secrets.scan_paths([str(env)], patterns, ["**/.env"])
        check("blocks a never-commit path", forbidden == [str(env)])


# --------------------------------------------------------------------------- test output parsing


def test_output_parsing() -> None:
    section("test output parsing")
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import run_tests  # noqa: PLC0415

    jest = "Tests:  3 failed, 47 passed, 2 skipped, 52 total"
    counts = run_tests.parse_counts(jest, "unit")
    check("jest/vitest style", counts == {"passed": 47, "failed": 3, "skipped": 2}, str(counts))

    pytest_out = "==== 12 passed, 1 failed, 3 skipped in 4.21s ===="
    counts = run_tests.parse_counts(pytest_out, "unit")
    check("pytest style", counts == {"passed": 12, "failed": 1, "skipped": 3}, str(counts))

    maven = "Tests run: 20, Failures: 2, Errors: 1, Skipped: 3"
    counts = run_tests.parse_counts(maven, "unit")
    check("maven surefire style", counts == {"passed": 14, "failed": 3, "skipped": 3}, str(counts))

    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "pw.json"
        report.write_text(
            json.dumps({"stats": {"expected": 9, "unexpected": 1, "flaky": 0, "skipped": 2}}),
            encoding="utf-8",
        )
        counts = run_tests.parse_playwright_json(report)
        check("playwright json report", counts == {"passed": 9, "failed": 1, "skipped": 2}, str(counts))


# --------------------------------------------------------------------------- main


def main() -> int:
    print("agent-coddies self-test")
    test_yaml()
    test_config()
    test_jira_markdown()
    test_sql_guard()
    test_state()
    test_secrets()
    test_output_parsing()
    print(f"\n{PASSED} passed, {FAILED} failed")
    if FAILED:
        print("\nThe toolkit is not sound on this machine. Fix the failures above before using it.")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
