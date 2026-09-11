#!/usr/bin/env python3
"""agent-coddies command line.

One entry point for all three subagents:

    config check | config show
    jira    get | search | comment | transition | transitions | link-mr | comments
    gitlab  whoami | mr-create | mr-update | mr-get | mr-note | pipeline-status | job-log
    db      schema | table | sample | query
    state   init | show | handoff | finding | resolve | verdict | tests | ship | block | findings-md
    secrets scan

Exit codes: 0 ok, 1 handled error, 2 bad usage, 3 configuration problem.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from coddie import __version__  # noqa: E402
from coddie.config import Config, ConfigError, YAML_BACKEND, config_dir, load, skill_dir  # noqa: E402
from coddie.http import HTTP_BACKEND  # noqa: E402
from coddie.state import RunState, StateError, slugify, state_root  # noqa: E402

OK, ERR, USAGE, CONFIG = 0, 1, 2, 3


def _out(text: str) -> None:
    sys.stdout.write(text.rstrip("\n") + "\n")


def _body(args) -> str:
    if getattr(args, "body_file", None):
        return Path(args.body_file).read_text(encoding="utf-8")
    if getattr(args, "body", None):
        return args.body
    if not sys.stdin.isatty():
        return sys.stdin.read()
    raise SystemExit("provide --body or --body-file (or pipe the text on stdin)")


# ============================================================================ config


def cmd_config_check(args, cfg: Config) -> int:
    problems = list(cfg.problems)
    _out(f"agent-coddies {__version__}")
    _out(f"  skill dir    {skill_dir()}")
    _out(f"  config dir   {config_dir()}")
    for key, path in cfg.sources.items():
        _out(f"  {key:12} {path}")
    _out(f"  yaml backend {YAML_BACKEND}    http backend {HTTP_BACKEND}")
    _out("")

    # Jira
    if cfg.get("jira.base_url"):
        _out(f"jira    {cfg.get('jira.base_url')}  ({cfg.get('jira.deployment', 'cloud')})")
        _out(f"        email {cfg.get('jira.email') or '(none)'}   token {Config.mask(cfg.get('jira.token'))}")
        if not args.offline:
            try:
                from coddie.jira import Jira

                me = Jira(cfg).whoami() or {}
                _out(f"        OK - authenticated as {me.get('displayName') or me.get('name') or '?'}")
            except Exception as exc:  # noqa: BLE001
                problems.append(f"jira: {cfg.scrub(str(exc).splitlines()[0])}")
                _out("        FAILED - see problems below")
    else:
        problems.append("jira.base_url is not set")

    # GitLab
    if cfg.get("gitlab.base_url"):
        _out(f"gitlab  {cfg.get('gitlab.base_url')}  project {cfg.get('gitlab.project')}")
        _out(f"        token {Config.mask(cfg.get('gitlab.token'))}")
        if not args.offline:
            try:
                from coddie.gitlab import GitLab

                gl = GitLab(cfg)
                me = gl.whoami() or {}
                proj = gl.project() or {}
                _out(f"        OK - {me.get('username', '?')} on {proj.get('path_with_namespace', '?')}")
            except Exception as exc:  # noqa: BLE001
                problems.append(f"gitlab: {cfg.scrub(str(exc).splitlines()[0])}")
                _out("        FAILED - see problems below")
    else:
        problems.append("gitlab.base_url is not set")

    # Database (optional)
    dbsec = cfg.section("database")
    if dbsec.get("enabled"):
        _out(f"db      {dbsec.get('dialect')} {dbsec.get('host', '')}/{dbsec.get('database', '')} (read-only)")
        if not args.offline:
            try:
                from coddie.db import Database

                db = Database(cfg)
                db.query("SELECT 1" if db.dialect != "oracle" else "SELECT 1 FROM dual", limit=1)
                db.close()
                _out("        OK - connected")
            except Exception as exc:  # noqa: BLE001
                problems.append(f"database: {cfg.scrub(str(exc).splitlines()[0])}")
                _out("        FAILED - see problems below")
    else:
        _out("db      disabled")

    envs = cfg.section("test_environments")
    _out(f"test    environments: {', '.join(envs) if envs else '(none configured)'}")
    _out("")

    if problems:
        _out("problems:")
        for p in problems:
            _out(f"  - {p}")
        return CONFIG
    _out("all good.")
    return OK


def cmd_config_show(args, cfg: Config) -> int:
    _out(json.dumps(cfg.redacted(), indent=2, ensure_ascii=False))
    return OK


# ============================================================================ jira


def _jira(cfg):
    from coddie.jira import Jira

    return Jira(cfg)


def cmd_jira_get(args, cfg: Config) -> int:
    from coddie.jira import issue_to_markdown

    fields = cfg.get("jira.fields") or None
    issue = _jira(cfg).get_issue(args.key, fields)
    if args.format == "json":
        _out(json.dumps(issue, indent=2, ensure_ascii=False))
    else:
        _out(issue_to_markdown(issue, cfg.get("jira.base_url", "")))
    if args.save:
        Path(args.save).write_text(issue_to_markdown(issue, cfg.get("jira.base_url", "")), encoding="utf-8")
        _out(f"\nsaved to {args.save}")
    return OK


def cmd_jira_search(args, cfg: Config) -> int:
    data = _jira(cfg).search(args.jql, args.limit, ["summary", "status", "assignee", "priority"])
    for issue in (data or {}).get("issues", []):
        f = issue.get("fields", {})
        _out(
            f"{issue['key']:14} {(f.get('status') or {}).get('name', ''):14} "
            f"{(f.get('priority') or {}).get('name', ''):10} {f.get('summary', '')}"
        )
    return OK


def cmd_jira_comments(args, cfg: Config) -> int:
    from coddie.jira import adf_to_text

    for c in _jira(cfg).comments(args.key, args.limit):
        author = (c.get("author") or {}).get("displayName", "?")
        body = c.get("body")
        text = adf_to_text(body) if isinstance(body, dict) else str(body or "")
        _out(f"--- {author}  {c.get('created', '')[:19]}")
        _out(text.strip())
        _out("")
    return OK


def cmd_jira_comment(args, cfg: Config) -> int:
    body = cfg.scrub(_body(args))
    if args.dry_run:
        _out("DRY RUN - would post to " + args.key + ":\n")
        _out(body)
        return OK
    result = _jira(cfg).add_comment(args.key, body)
    _out(f"commented on {args.key} (id {(result or {}).get('id', '?')})")
    return OK


def cmd_jira_transitions(args, cfg: Config) -> int:
    for tr in _jira(cfg).transitions(args.key):
        _out(f"{tr.get('id'):>5}  {tr.get('name'):24} -> {(tr.get('to') or {}).get('name', '')}")
    return OK


def cmd_jira_transition(args, cfg: Config) -> int:
    if args.dry_run:
        _out(f"DRY RUN - would transition {args.key} to '{args.to}'")
        return OK
    name = _jira(cfg).transition(args.key, args.to)
    _out(f"{args.key} transitioned via '{name}'")
    return OK


def cmd_jira_link_mr(args, cfg: Config) -> int:
    if args.dry_run:
        _out(f"DRY RUN - would link {args.url} to {args.key}")
        return OK
    _jira(cfg).add_remote_link(args.key, args.url, args.title, args.summary or "")
    _out(f"linked {args.url} to {args.key}")
    return OK


# ============================================================================ gitlab


def _gitlab(cfg):
    from coddie.gitlab import GitLab

    return GitLab(cfg)


def cmd_gitlab_whoami(args, cfg: Config) -> int:
    gl = _gitlab(cfg)
    me, proj = gl.whoami() or {}, gl.project() or {}
    _out(f"user    {me.get('username')} ({me.get('name')})  id {me.get('id')}")
    _out(f"project {proj.get('path_with_namespace')}  id {proj.get('id')}")
    _out(f"default branch {proj.get('default_branch')}")
    return OK


def cmd_gitlab_mr_create(args, cfg: Config) -> int:
    gl = _gitlab(cfg)
    target = args.target or cfg.get("gitlab.default_target_branch", "main")
    source = args.source or _git("rev-parse --abbrev-ref HEAD")
    protected = [str(b).lower() for b in cfg.get("gitlab.protected_branches", [])]
    if source.lower() in protected:
        _out(f"refusing: source branch '{source}' is protected")
        return ERR

    description = Path(args.description_file).read_text(encoding="utf-8") if args.description_file else (args.description or "")
    description = cfg.scrub(description)
    labels = list(args.label or []) + list(cfg.get("gitlab.default_labels", []) or [])
    reviewers = list(args.reviewer or []) or list(cfg.get("gitlab.default_reviewers", []) or [])

    if args.dry_run:
        _out(f"DRY RUN - merge request\n  {source} -> {target}\n  title: {args.title}")
        _out(f"  labels: {', '.join(labels) or '(none)'}   reviewers: {', '.join(reviewers) or '(none)'}")
        _out("\n" + description)
        return OK

    assignee_ids = [gl.whoami()["id"]] if args.assignee_me else None
    reviewer_ids = gl.resolve_users(reviewers) or None
    mr = gl.create_mr(
        source=source, target=target, title=args.title, description=description,
        labels=labels or None, assignee_ids=assignee_ids, reviewer_ids=reviewer_ids,
        remove_source_branch=args.remove_source_branch or cfg.get("gitlab.remove_source_branch", True),
        squash=cfg.get("gitlab.squash_on_merge", True), draft=args.draft,
    )
    _out(f"created !{mr['iid']}  {mr['web_url']}")
    _out(f"  {mr['source_branch']} -> {mr['target_branch']}   state {mr['state']}")
    return OK


def cmd_gitlab_mr_update(args, cfg: Config) -> int:
    description = Path(args.description_file).read_text(encoding="utf-8") if args.description_file else args.description
    mr = _gitlab(cfg).update_mr(
        args.iid,
        title=args.title,
        description=cfg.scrub(description) if description else None,
        labels=list(args.label) if args.label else None,
    )
    _out(f"updated !{mr['iid']}  {mr['web_url']}")
    return OK


def cmd_gitlab_mr_get(args, cfg: Config) -> int:
    mr = _gitlab(cfg).get_mr(args.iid)
    _out(f"!{mr['iid']}  {mr['title']}")
    _out(f"  {mr['source_branch']} -> {mr['target_branch']}   state {mr['state']}")
    _out(f"  author {(mr.get('author') or {}).get('username')}   url {mr['web_url']}")
    _out(f"  merge status: {mr.get('detailed_merge_status') or mr.get('merge_status')}")
    if mr.get("has_conflicts"):
        _out("  !! has conflicts")
    return OK


def cmd_gitlab_mr_note(args, cfg: Config) -> int:
    body = cfg.scrub(_body(args))
    if args.dry_run:
        _out(f"DRY RUN - would comment on !{args.iid}:\n\n{body}")
        return OK
    _gitlab(cfg).mr_note(args.iid, body)
    _out(f"commented on !{args.iid}")
    return OK


def cmd_gitlab_pipeline_status(args, cfg: Config) -> int:
    gl = _gitlab(cfg)
    pipelines = gl.mr_pipelines(args.mr)
    if not pipelines:
        _out("no pipeline has run for this merge request yet")
        return OK
    latest = pipelines[0]
    _out(f"pipeline {latest['id']}  status {latest['status']}  {latest.get('web_url', '')}")
    if args.jobs or latest["status"] in ("failed", "canceled"):
        for job in gl.pipeline_jobs(latest["id"]):
            flag = "x" if job["status"] == "failed" else ("." if job["status"] == "success" else "?")
            _out(f"  [{flag}] {job['stage']:12} {job['name']:28} {job['status']}")
    return ERR if latest["status"] == "failed" else OK


def cmd_gitlab_job_log(args, cfg: Config) -> int:
    _out(cfg.scrub(_gitlab(cfg).job_trace(args.job, args.tail)))
    return OK


# ============================================================================ db


def cmd_db_schema(args, cfg: Config) -> int:
    from coddie.db import Database, render_table

    db = Database(cfg)
    try:
        cols, rows = db.tables(args.like)
        _out(render_table(cols, rows))
    finally:
        db.close()
    return OK


def cmd_db_table(args, cfg: Config) -> int:
    from coddie.db import Database, render_table

    db = Database(cfg)
    try:
        _out(f"== {args.table} : columns")
        _out(render_table(*db.columns(args.table)))
        _out("")
        _out(f"== {args.table} : keys")
        _out(render_table(*db.keys(args.table)))
        if args.count:
            _out("")
            _out(f"== {args.table} : {db.row_count(args.table)} rows")
    finally:
        db.close()
    return OK


def cmd_db_sample(args, cfg: Config) -> int:
    from coddie.db import Database, render_table

    db = Database(cfg)
    try:
        _out(render_table(*db.sample(args.table, args.limit)))
        _out("(columns matching database.redact_columns are masked)")
    finally:
        db.close()
    return OK


def cmd_db_query(args, cfg: Config) -> int:
    from coddie.db import Database, render_table

    sql = Path(args.file).read_text(encoding="utf-8") if args.file else args.sql
    db = Database(cfg)
    try:
        cols, rows = db.query_redacted(sql, limit=args.limit)
        _out(render_table(cols, rows))
    finally:
        db.close()
    return OK


# ============================================================================ state


def _state(cfg, ticket: str, create: bool = False) -> RunState:
    return RunState.open(ticket, state_root(cfg), create=create)


def cmd_state_init(args, cfg: Config) -> int:
    st = _state(cfg, args.ticket, create=True)
    scope = Path(args.scope_file).read_text(encoding="utf-8") if args.scope_file else (args.scope or "")
    branch = args.branch or _git("rev-parse --abbrev-ref HEAD", allow_fail=True)
    st.init(args.summary or "", branch, scope)
    st.save()
    _out(f"ledger at {st.path}")
    _out(st.summary_text())
    return OK


def cmd_state_show(args, cfg: Config) -> int:
    st = _state(cfg, args.ticket)
    _out(json.dumps(st.data, indent=2, ensure_ascii=False) if args.json else st.summary_text())
    return OK


def cmd_state_handoff(args, cfg: Config) -> int:
    st = _state(cfg, args.ticket)
    st.handoff(args.to, args.note or "", args.by or "")
    st.save()
    limit = int(cfg.get("workflow.max_fix_iterations", 3))
    if st.data["round"] > limit:
        _out(f"WARNING: round {st.data['round']} exceeds workflow.max_fix_iterations ({limit}).")
        _out("Stop the loop and report the open findings to the user.")
    _out(f"{st.ticket}: handed to {args.to} (round {st.data['round']})")
    return OK


def cmd_state_finding(args, cfg: Config) -> int:
    st = _state(cfg, args.ticket)
    fid = st.add_finding(
        severity=args.severity, title=args.title, where=args.where,
        repro=args.repro, expected=args.expected, actual=args.actual, artifact=args.artifact,
    )
    st.save()
    _out(f"{st.ticket}: recorded {fid} ({args.severity}) {args.title}")
    return OK


def cmd_state_resolve(args, cfg: Config) -> int:
    st = _state(cfg, args.ticket)
    finding = st.resolve_finding(args.finding, args.note or "", args.reject)
    st.save()
    _out(f"{st.ticket}: {finding['id']} marked {finding['status']}")
    remaining = st.open_findings()
    _out(f"  {len(remaining)} finding(s) still open" if remaining else "  all findings closed")
    return OK


def cmd_state_verdict(args, cfg: Config) -> int:
    if args.passed == args.fail:  # neither, or both
        _out("pass exactly one of --pass or --fail")
        return USAGE
    st = _state(cfg, args.ticket)
    st.set_verdict(passed=args.passed, note=args.note or "")
    st.save()
    _out(f"{st.ticket}: verdict {st.data['verdict']}")
    return OK


def cmd_state_tests(args, cfg: Config) -> int:
    st = _state(cfg, args.ticket)
    st.record_tests(
        runner=args.runner, passed=args.passed, failed=args.failed,
        skipped=args.skipped, command=args.command or "", report=args.report or "",
    )
    st.save()
    _out(f"{st.ticket}: recorded {args.runner} {args.passed} passed / {args.failed} failed")
    return OK


def cmd_state_ship(args, cfg: Config) -> int:
    st = _state(cfg, args.ticket)
    if st.data.get("verdict") != "PASS":
        _out(f"refusing: verdict is {st.data.get('verdict') or 'not set'}, expected PASS")
        return ERR
    st.ship(args.mr_url, args.mr_iid or "", args.commit or "", args.jira_status or "")
    st.save()
    _out(f"{st.ticket}: delivered - {args.mr_url}")
    return OK


def cmd_state_block(args, cfg: Config) -> int:
    st = _state(cfg, args.ticket)
    st.block(args.reason)
    st.save()
    _out(f"{st.ticket}: BLOCKED - {args.reason}")
    return OK


def cmd_state_findings_md(args, cfg: Config) -> int:
    _out(_state(cfg, args.ticket).findings_markdown(only_open=not args.all))
    return OK


def cmd_state_branch_name(args, cfg: Config) -> int:
    pattern = cfg.get("gitlab.branch_pattern", "{ticket}-{slug}")
    _out(pattern.format(ticket=args.ticket.upper(), slug=slugify(args.summary)))
    return OK


# ============================================================================ secrets


def cmd_secrets_scan(args, cfg: Config) -> int:
    from coddie.secrets import changed_files, scan_paths

    paths = args.path or changed_files(staged=args.staged, base=args.base)
    if not paths:
        _out("nothing to scan (no changed files)")
        return OK
    hits, forbidden = scan_paths(
        paths, cfg.get("security.secret_patterns", []), cfg.get("security.never_commit", [])
    )
    for path in forbidden:
        _out(f"FORBIDDEN PATH  {path}  (matches security.never_commit)")
    for hit in hits:
        _out(f"POSSIBLE SECRET {hit}")
    if hits or forbidden:
        _out("")
        _out(f"{len(hits)} possible secret(s), {len(forbidden)} forbidden path(s) across {len(paths)} file(s).")
        _out("Do not push. Review each hit; remove the value and rotate it if it is real.")
        return ERR
    _out(f"clean - {len(paths)} file(s) scanned, no secrets matched")
    return OK


# ============================================================================ git helper


def _git(args_str: str, allow_fail: bool = False) -> str:
    proc = subprocess.run(["git", *args_str.split()], capture_output=True, text=True)
    if proc.returncode != 0:
        if allow_fail:
            return ""
        raise SystemExit(f"git {args_str} failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


# ============================================================================ parser


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="coddie_cli.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=f"agent-coddies {__version__}")
    sub = p.add_subparsers(dest="group", required=True)

    # ---- config
    g = sub.add_parser("config", help="inspect configuration").add_subparsers(dest="cmd", required=True)
    c = g.add_parser("check", help="validate credentials and connectivity")
    c.add_argument("--offline", action="store_true", help="skip network checks")
    c.set_defaults(func=cmd_config_check, strict=False)
    c = g.add_parser("show", help="print the merged config with secrets masked")
    c.set_defaults(func=cmd_config_show, strict=False)

    # ---- jira
    g = sub.add_parser("jira", help="Jira operations").add_subparsers(dest="cmd", required=True)
    c = g.add_parser("get", help="fetch an issue")
    c.add_argument("key")
    c.add_argument("--format", choices=["md", "json"], default="md")
    c.add_argument("--save", help="also write the markdown to this path")
    c.set_defaults(func=cmd_jira_get)
    c = g.add_parser("search", help="run JQL")
    c.add_argument("jql")
    c.add_argument("--limit", type=int, default=20)
    c.set_defaults(func=cmd_jira_search)
    c = g.add_parser("comments", help="list comments")
    c.add_argument("key")
    c.add_argument("--limit", type=int, default=20)
    c.set_defaults(func=cmd_jira_comments)
    c = g.add_parser("comment", help="add a comment (markdown in, ADF/wiki out)")
    c.add_argument("key")
    c.add_argument("--body")
    c.add_argument("--body-file")
    c.add_argument("--dry-run", action="store_true")
    c.set_defaults(func=cmd_jira_comment)
    c = g.add_parser("transitions", help="list available transitions")
    c.add_argument("key")
    c.set_defaults(func=cmd_jira_transitions)
    c = g.add_parser("transition", help="move an issue to a status")
    c.add_argument("key")
    c.add_argument("--to", required=True)
    c.add_argument("--dry-run", action="store_true")
    c.set_defaults(func=cmd_jira_transition)
    c = g.add_parser("link-mr", help="attach a merge request as a remote link")
    c.add_argument("key")
    c.add_argument("--url", required=True)
    c.add_argument("--title", required=True)
    c.add_argument("--summary")
    c.add_argument("--dry-run", action="store_true")
    c.set_defaults(func=cmd_jira_link_mr)

    # ---- gitlab
    g = sub.add_parser("gitlab", help="GitLab operations").add_subparsers(dest="cmd", required=True)
    c = g.add_parser("whoami", help="show the authenticated user and project")
    c.set_defaults(func=cmd_gitlab_whoami)
    c = g.add_parser("mr-create", help="open a merge request")
    c.add_argument("--source", help="default: current branch")
    c.add_argument("--target", help="default: gitlab.default_target_branch")
    c.add_argument("--title", required=True)
    c.add_argument("--description")
    c.add_argument("--description-file")
    c.add_argument("--label", action="append")
    c.add_argument("--reviewer", action="append", help="GitLab username; repeatable")
    c.add_argument("--assignee-me", action="store_true")
    c.add_argument("--remove-source-branch", action="store_true")
    c.add_argument("--draft", action="store_true")
    c.add_argument("--dry-run", action="store_true")
    c.set_defaults(func=cmd_gitlab_mr_create)
    c = g.add_parser("mr-update", help="edit an existing merge request")
    c.add_argument("--iid", type=int, required=True)
    c.add_argument("--title")
    c.add_argument("--description")
    c.add_argument("--description-file")
    c.add_argument("--label", action="append")
    c.set_defaults(func=cmd_gitlab_mr_update)
    c = g.add_parser("mr-get", help="show a merge request")
    c.add_argument("iid", type=int)
    c.set_defaults(func=cmd_gitlab_mr_get)
    c = g.add_parser("mr-note", help="comment on a merge request")
    c.add_argument("iid", type=int)
    c.add_argument("--body")
    c.add_argument("--body-file")
    c.add_argument("--dry-run", action="store_true")
    c.set_defaults(func=cmd_gitlab_mr_note)
    c = g.add_parser("pipeline-status", help="latest pipeline for a merge request")
    c.add_argument("--mr", type=int, required=True)
    c.add_argument("--jobs", action="store_true")
    c.set_defaults(func=cmd_gitlab_pipeline_status)
    c = g.add_parser("job-log", help="tail a job log")
    c.add_argument("job", type=int)
    c.add_argument("--tail", type=int, default=200)
    c.set_defaults(func=cmd_gitlab_job_log)

    # ---- db
    g = sub.add_parser("db", help="read-only schema introspection").add_subparsers(dest="cmd", required=True)
    c = g.add_parser("schema", help="list tables")
    c.add_argument("--like", help="SQL LIKE pattern, e.g. %%partner%%")
    c.set_defaults(func=cmd_db_schema)
    c = g.add_parser("table", help="columns and keys of one table")
    c.add_argument("table")
    c.add_argument("--count", action="store_true", help="also report the row count")
    c.set_defaults(func=cmd_db_table)
    c = g.add_parser("sample", help="sample rows with sensitive columns masked")
    c.add_argument("table")
    c.add_argument("--limit", type=int, default=5)
    c.set_defaults(func=cmd_db_sample)
    c = g.add_parser("query", help="run one SELECT")
    c.add_argument("sql", nargs="?")
    c.add_argument("--file")
    c.add_argument("--limit", type=int, default=100)
    c.set_defaults(func=cmd_db_query)

    # ---- state
    g = sub.add_parser("state", help="the run ledger shared by the three agents").add_subparsers(dest="cmd", required=True)
    c = g.add_parser("init", help="start a run")
    c.add_argument("ticket")
    c.add_argument("--summary")
    c.add_argument("--branch")
    c.add_argument("--scope")
    c.add_argument("--scope-file")
    c.set_defaults(func=cmd_state_init, strict=False)
    c = g.add_parser("show", help="print the ledger")
    c.add_argument("ticket")
    c.add_argument("--json", action="store_true")
    c.set_defaults(func=cmd_state_show, strict=False)
    c = g.add_parser("handoff", help="hand the run to another agent")
    c.add_argument("ticket")
    c.add_argument("--to", required=True, choices=["dev", "qa", "ship"])
    c.add_argument("--note")
    c.add_argument("--by")
    c.set_defaults(func=cmd_state_handoff, strict=False)
    c = g.add_parser("finding", help="record a QA finding")
    c.add_argument("ticket")
    c.add_argument("--severity", required=True, choices=["blocker", "high", "medium", "low"])
    c.add_argument("--title", required=True)
    c.add_argument("--where")
    c.add_argument("--repro")
    c.add_argument("--expected")
    c.add_argument("--actual")
    c.add_argument("--artifact")
    c.set_defaults(func=cmd_state_finding, strict=False)
    c = g.add_parser("resolve", help="close a finding as fixed or rejected")
    c.add_argument("ticket")
    c.add_argument("--finding", required=True)
    c.add_argument("--note")
    c.add_argument("--reject", action="store_true")
    c.set_defaults(func=cmd_state_resolve, strict=False)
    c = g.add_parser("verdict", help="record the QA verdict")
    c.add_argument("ticket")
    c.add_argument("--pass", dest="passed", action="store_true")
    c.add_argument("--fail", action="store_true")
    c.add_argument("--note")
    c.set_defaults(func=cmd_state_verdict, strict=False)
    c = g.add_parser("tests", help="record a test run")
    c.add_argument("ticket")
    c.add_argument("--runner", required=True)
    c.add_argument("--passed", type=int, default=0)
    c.add_argument("--failed", type=int, default=0)
    c.add_argument("--skipped", type=int, default=0)
    c.add_argument("--command")
    c.add_argument("--report")
    c.set_defaults(func=cmd_state_tests, strict=False)
    c = g.add_parser("ship", help="record delivery")
    c.add_argument("ticket")
    c.add_argument("--mr-url", required=True)
    c.add_argument("--mr-iid")
    c.add_argument("--commit")
    c.add_argument("--jira-status")
    c.set_defaults(func=cmd_state_ship, strict=False)
    c = g.add_parser("block", help="mark the run blocked")
    c.add_argument("ticket")
    c.add_argument("--reason", required=True)
    c.set_defaults(func=cmd_state_block, strict=False)
    c = g.add_parser("findings-md", help="render findings as markdown")
    c.add_argument("ticket")
    c.add_argument("--all", action="store_true")
    c.set_defaults(func=cmd_state_findings_md, strict=False)
    c = g.add_parser("branch-name", help="suggest a branch name from the configured pattern")
    c.add_argument("ticket")
    c.add_argument("--summary", required=True)
    c.set_defaults(func=cmd_state_branch_name, strict=False)

    # ---- secrets
    g = sub.add_parser("secrets", help="pre-push secret scanning").add_subparsers(dest="cmd", required=True)
    c = g.add_parser("scan", help="scan changed files for credentials")
    c.add_argument("path", nargs="*", help="explicit paths (default: changed files)")
    c.add_argument("--staged", action="store_true", default=True)
    c.add_argument("--base", help="scan everything changed since this ref")
    c.set_defaults(func=cmd_secrets_scan, strict=False)

    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        cfg = load(strict=getattr(args, "strict", True))
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return CONFIG
    try:
        return args.func(args, cfg)
    except StateError as exc:
        print(f"state error: {exc}", file=sys.stderr)
        return ERR
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return CONFIG
    except KeyboardInterrupt:
        return ERR
    except Exception as exc:  # noqa: BLE001 - surface a clean, scrubbed message
        message = cfg.scrub(str(exc)) if cfg else str(exc)
        print(f"error: {message}", file=sys.stderr)
        if "--traceback" in sys.argv:
            raise
        return ERR


if __name__ == "__main__":
    sys.exit(main())
