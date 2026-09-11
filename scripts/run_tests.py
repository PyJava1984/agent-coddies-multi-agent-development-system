#!/usr/bin/env python3
"""Test runners for coddie-qa: Playwright, Selenium, and the project's own suite.

    run_tests.py playwright --spec tests/e2e --browser chromium
    run_tests.py selenium   --spec tests/selenium --browser chrome
    run_tests.py unit
    run_tests.py scaffold playwright --out tests/e2e     # write a starter spec
    run_tests.py env --show                              # resolved base URL / account

Environment variables exported to the child process, from
credentials.yaml -> test_environments.<env>:

    CODDIE_BASE_URL, CODDIE_USERNAME, CODDIE_PASSWORD, CODDIE_ENV

so specs never hardcode a URL or an account. Results are parsed into
passed/failed/skipped counts and can be written straight into the run ledger
with --ticket.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from coddie.config import ConfigError, load, skill_dir  # noqa: E402
from coddie.state import RunState, state_root  # noqa: E402

OK, ERR, USAGE = 0, 1, 2


# --------------------------------------------------------------------------- helpers


def resolve_env(cfg, name: str = None) -> dict:
    envs = cfg.section("test_environments")
    name = name or cfg.get("testing.default_environment", "dev")
    if name.lower() in ("prod", "production"):
        raise SystemExit("refusing to run automated tests against production")
    if name not in envs:
        raise SystemExit(
            f"test environment '{name}' is not in credentials.yaml -> test_environments "
            f"(have: {', '.join(envs) or 'none'})"
        )
    env = dict(envs[name])
    env["name"] = name
    return env


def child_env(cfg, env: dict) -> dict:
    out = dict(os.environ)
    out["CODDIE_ENV"] = env.get("name", "")
    out["CODDIE_BASE_URL"] = str(env.get("base_url", ""))
    out["CODDIE_USERNAME"] = str(env.get("username", ""))
    out["CODDIE_PASSWORD"] = str(env.get("password", ""))
    # Common aliases so existing suites pick them up without edits.
    out.setdefault("BASE_URL", out["CODDIE_BASE_URL"])
    out.setdefault("PLAYWRIGHT_BASE_URL", out["CODDIE_BASE_URL"])
    return out


def run(cmd, cwd=None, env=None, echo=True) -> tuple:
    """Run a command, stream nothing, return (rc, combined_output)."""
    printable = cmd if isinstance(cmd, str) else " ".join(cmd)
    if echo:
        print(f"$ {printable}")
    proc = subprocess.run(
        cmd, cwd=cwd, env=env, shell=isinstance(cmd, str),
        capture_output=True, text=True, errors="replace",
    )
    output = (proc.stdout or "") + (proc.stderr or "")
    print(output.rstrip())
    return proc.returncode, output


def have(tool: str) -> bool:
    return shutil.which(tool) is not None


def artifacts_dir(cfg, ticket: str = None) -> Path:
    root = state_root(cfg)
    path = (root / ticket.upper() / "artifacts") if ticket else (root / "artifacts")
    path.mkdir(parents=True, exist_ok=True)
    return path


# --------------------------------------------------------------------------- parsing


def parse_counts(output: str, runner: str) -> dict:
    """Best-effort extraction of passed/failed/skipped from runner output."""
    counts = {"passed": 0, "failed": 0, "skipped": 0}
    text = output.replace("✓", "").replace("✗", "")

    for key, patterns in {
        "passed": [r"(\d+)\s+passed", r"(\d+)\s+tests?\s+passed", r"Tests run: \d+.*?Failures: \d+"],
        "failed": [r"(\d+)\s+failed", r"(\d+)\s+failures?", r"(\d+)\s+error"],
        "skipped": [r"(\d+)\s+skipped", r"(\d+)\s+pending", r"(\d+)\s+did not run"],
    }.items():
        for pattern in patterns:
            m = re.search(pattern, text, re.IGNORECASE)
            if m and m.groups():
                counts[key] = max(counts[key], int(m.group(1)))
                break

    # Maven surefire: "Tests run: 12, Failures: 1, Errors: 0, Skipped: 2"
    m = re.search(
        r"Tests run:\s*(\d+),\s*Failures:\s*(\d+),\s*Errors:\s*(\d+),\s*Skipped:\s*(\d+)", text
    )
    if m:
        total, fail, err, skip = (int(g) for g in m.groups())
        counts = {"passed": total - fail - err - skip, "failed": fail + err, "skipped": skip}
    return counts


def parse_playwright_json(report: Path) -> dict:
    try:
        data = json.loads(report.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    stats = data.get("stats") or {}
    if stats:
        return {
            "passed": int(stats.get("expected", 0)),
            "failed": int(stats.get("unexpected", 0)) + int(stats.get("flaky", 0) or 0),
            "skipped": int(stats.get("skipped", 0)),
        }
    return {}


def record(cfg, ticket: str, runner: str, counts: dict, command: str, report: str = "") -> None:
    if not ticket:
        return
    try:
        st = RunState.open(ticket, state_root(cfg))
    except Exception as exc:  # noqa: BLE001 - ledger is optional here
        print(f"(not recorded in the ledger: {exc})")
        return
    st.record_tests(runner=runner, command=command, report=report, **counts)
    st.save()
    print(f"recorded in {st.path}")


# --------------------------------------------------------------------------- runners


def cmd_playwright(args, cfg) -> int:
    env_cfg = resolve_env(cfg, args.env)
    environ = child_env(cfg, env_cfg)
    pw = cfg.section("testing").get("playwright", {}) if cfg.section("testing") else {}
    out_dir = artifacts_dir(cfg, args.ticket)
    report = out_dir / "playwright-report.json"

    project_root = Path(args.cwd or ".").resolve()
    is_node = (project_root / "package.json").exists()
    is_python = args.python or not is_node

    if args.install:
        if is_node:
            run(["npx", "--yes", "playwright", "install", "--with-deps"], cwd=project_root, env=environ)
        else:
            run([sys.executable, "-m", "playwright", "install"], env=environ)

    if is_python:
        if not have("pytest"):
            print("pytest is not installed - pip install pytest pytest-playwright")
            return ERR
        cmd = [sys.executable, "-m", "pytest", args.spec or "tests", "-q",
               f"--browser={args.browser or 'chromium'}"]
        if not (args.headed if args.headed is not None else pw.get("headed", False)):
            pass  # pytest-playwright is headless by default
        else:
            cmd.append("--headed")
        if args.grep:
            cmd += ["-k", args.grep]
        rc, output = run(cmd, cwd=project_root, env=environ)
        counts = parse_counts(output, "playwright")
    else:
        cmd = ["npx", "--yes", "playwright", "test"]
        if args.spec:
            cmd.append(args.spec)
        cmd += [f"--project={args.browser}"] if args.browser else []
        cmd += ["--reporter=list,json"]
        if args.grep:
            cmd += ["--grep", args.grep]
        if args.headed if args.headed is not None else pw.get("headed", False):
            cmd.append("--headed")
        workers = args.workers or pw.get("workers")
        if workers:
            cmd.append(f"--workers={workers}")
        retries = pw.get("retries")
        if retries is not None:
            cmd.append(f"--retries={retries}")
        trace = pw.get("trace")
        if trace:
            cmd.append(f"--trace={trace}")
        environ["PLAYWRIGHT_JSON_OUTPUT_NAME"] = str(report)
        rc, output = run(cmd, cwd=project_root, env=environ)
        counts = parse_playwright_json(report) or parse_counts(output, "playwright")

    print(f"\nplaywright: {counts['passed']} passed, {counts['failed']} failed, {counts['skipped']} skipped")
    record(cfg, args.ticket, "playwright", counts, " ".join(cmd), str(report if report.exists() else ""))
    return OK if rc == 0 and counts["failed"] == 0 else ERR


def cmd_selenium(args, cfg) -> int:
    env_cfg = resolve_env(cfg, args.env)
    environ = child_env(cfg, env_cfg)
    sel = cfg.section("testing").get("selenium", {}) if cfg.section("testing") else {}
    environ["CODDIE_SELENIUM_BROWSER"] = args.browser or (sel.get("browsers") or ["chrome"])[0]
    environ["CODDIE_SELENIUM_HEADLESS"] = str(
        sel.get("headless", True) if args.headed is None else (not args.headed)
    ).lower()
    if sel.get("remote_url"):
        environ["CODDIE_SELENIUM_REMOTE_URL"] = str(sel["remote_url"])

    project_root = Path(args.cwd or ".").resolve()
    spec = args.spec or "tests/selenium"

    if args.command:
        cmd = args.command
    elif (project_root / "pom.xml").exists() and not Path(spec).suffix == ".py":
        cmd = ["mvn", "-q", "test", f"-Dtest={args.grep}"] if args.grep else ["mvn", "-q", "test"]
    elif (project_root / "package.json").exists() and Path(spec).suffix in (".js", ".ts", ""):
        cmd = ["npx", "--yes", "mocha", spec, "--timeout", "60000"]
    else:
        if not have("pytest"):
            print("pytest is not installed - pip install pytest selenium")
            return ERR
        cmd = [sys.executable, "-m", "pytest", spec, "-q"]
        if args.grep:
            cmd += ["-k", args.grep]

    rc, output = run(cmd, cwd=project_root, env=environ)
    counts = parse_counts(output, "selenium")
    print(f"\nselenium: {counts['passed']} passed, {counts['failed']} failed, {counts['skipped']} skipped")
    record(cfg, args.ticket, "selenium", counts, cmd if isinstance(cmd, str) else " ".join(cmd))
    return OK if rc == 0 and counts["failed"] == 0 else ERR


def cmd_unit(args, cfg) -> int:
    project_root = Path(args.cwd or ".").resolve()
    candidates = args.command and [args.command] or cfg.get("testing.unit_test_commands", [])
    markers = {
        "npm": "package.json", "pnpm": "package.json", "yarn": "package.json",
        "pytest": None, "mvn": "pom.xml", "./gradlew": "gradlew", "dotnet": None,
    }
    for candidate in candidates:
        tool = str(candidate).split()[0]
        marker = markers.get(tool, None)
        if marker and not (project_root / marker).exists():
            continue
        if tool not in ("./gradlew",) and not have(tool.replace("./", "")):
            continue
        rc, output = run(str(candidate), cwd=project_root, env=os.environ.copy())
        counts = parse_counts(output, "unit")
        print(f"\nunit: {counts['passed']} passed, {counts['failed']} failed, {counts['skipped']} skipped")
        record(cfg, args.ticket, "unit", counts, str(candidate))
        return OK if rc == 0 else ERR
    print("no runnable unit-test command found for this project.")
    print("Tried: " + ", ".join(str(c) for c in candidates))
    print("Pass one explicitly with --command, or add it to config.yaml -> testing.unit_test_commands")
    return ERR


def cmd_env(args, cfg) -> int:
    env_cfg = resolve_env(cfg, args.env)
    print(f"environment  {env_cfg['name']}")
    print(f"base_url     {env_cfg.get('base_url')}")
    print(f"username     {env_cfg.get('username')}")
    print(f"password     {'(set)' if env_cfg.get('password') else '(unset)'}")
    print("\nexported to test processes as CODDIE_BASE_URL / CODDIE_USERNAME / CODDIE_PASSWORD")
    return OK


def cmd_scaffold(args, cfg) -> int:
    templates = skill_dir() / "templates" / "tests"
    name = args.runner
    src = templates / (f"{name}.spec.ts" if name == "playwright" else f"test_{name}.py")
    if not src.exists():
        print(f"no starter template for '{name}' at {src}")
        return ERR
    out_dir = Path(args.out or ("tests/e2e" if name == "playwright" else "tests/selenium"))
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / src.name
    if dest.exists() and not args.force:
        print(f"{dest} already exists - pass --force to overwrite")
        return ERR
    dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"wrote {dest}")
    print("It reads CODDIE_BASE_URL / CODDIE_USERNAME / CODDIE_PASSWORD - no hardcoded values.")
    return OK


# --------------------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="run_tests.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("--env", help="test_environments key (default: testing.default_environment)")
        sp.add_argument("--ticket", help="record the result in this ticket's run ledger")
        sp.add_argument("--cwd", help="project root (default: current directory)")
        sp.add_argument("--grep", help="only run tests matching this")

    c = sub.add_parser("playwright", help="run Playwright specs")
    common(c)
    c.add_argument("--spec", help="file or directory of specs")
    c.add_argument("--browser", help="chromium | firefox | webkit (node) or the pytest --browser value")
    c.add_argument("--headed", type=lambda v: v.lower() not in ("false", "0", "no"), nargs="?", const=True)
    c.add_argument("--workers", type=int)
    c.add_argument("--python", action="store_true", help="force the pytest-playwright runner")
    c.add_argument("--install", action="store_true", help="install browsers first")
    c.set_defaults(func=cmd_playwright)

    c = sub.add_parser("selenium", help="run Selenium tests")
    common(c)
    c.add_argument("--spec", help="file or directory of tests")
    c.add_argument("--browser", help="chrome | firefox | edge")
    c.add_argument("--headed", type=lambda v: v.lower() not in ("false", "0", "no"), nargs="?", const=True)
    c.add_argument("--command", help="explicit command to run instead of the detected one")
    c.set_defaults(func=cmd_selenium)

    c = sub.add_parser("unit", help="run the project's own test suite")
    common(c)
    c.add_argument("--command", help="explicit command")
    c.set_defaults(func=cmd_unit)

    c = sub.add_parser("env", help="show the resolved test environment")
    c.add_argument("--env")
    c.add_argument("--show", action="store_true")
    c.set_defaults(func=cmd_env)

    c = sub.add_parser("scaffold", help="write a starter spec that uses the configured environment")
    c.add_argument("runner", choices=["playwright", "selenium"])
    c.add_argument("--out")
    c.add_argument("--force", action="store_true")
    c.set_defaults(func=cmd_scaffold)

    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        cfg = load(strict=False)
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return ERR
    try:
        return args.func(args, cfg)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"error: {cfg.scrub(str(exc))}", file=sys.stderr)
        return ERR


if __name__ == "__main__":
    sys.exit(main())
