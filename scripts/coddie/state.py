"""The run ledger: the shared state the three coddie agents hand between them.

One JSON file per ticket at <state_dir>/<TICKET>/state.json, plus an append-only
events log so a run can be audited after the fact.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SEVERITIES = ("blocker", "high", "medium", "low")
STAGES = ("intake", "dev", "qa", "ship", "done", "blocked")


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def slugify(text: str, max_len: int = 40) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", (text or "").lower()).strip("-")
    if len(slug) > max_len:
        slug = slug[:max_len].rstrip("-")
    return slug or "change"


class StateError(RuntimeError):
    pass


class RunState:
    def __init__(self, ticket: str, root: Path):
        self.ticket = ticket.upper()
        self.dir = root / self.ticket
        self.path = self.dir / "state.json"
        self.events_path = self.dir / "events.jsonl"
        self.artifacts = self.dir / "artifacts"
        self.data: dict = {}

    # ------------------------------------------------------------------ io

    @classmethod
    def open(cls, ticket: str, root: Path, create: bool = False) -> "RunState":
        st = cls(ticket, root)
        if st.path.is_file():
            st.data = json.loads(st.path.read_text(encoding="utf-8"))
            return st
        if not create:
            raise StateError(
                f"no run ledger for {st.ticket} at {st.path}\n"
                f"  start one with:  state init {st.ticket} --summary \"...\""
            )
        st.data = {
            "ticket": st.ticket,
            "summary": "",
            "created_at": now(),
            "updated_at": now(),
            "stage": "intake",
            "round": 0,
            "verdict": None,
            "branch": "",
            "scope": "",
            "findings": [],
            "handoffs": [],
            "test_runs": [],
            "delivery": {},
        }
        return st

    def save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self.artifacts.mkdir(parents=True, exist_ok=True)
        self.data["updated_at"] = now()
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)

    def event(self, kind: str, **payload) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        record = {"at": now(), "kind": kind, **payload}
        with self.events_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    # ------------------------------------------------------------------ mutations

    def init(self, summary: str, branch: str = "", scope: str = "") -> None:
        self.data["summary"] = summary or self.data.get("summary", "")
        self.data["branch"] = branch or self.data.get("branch", "")
        self.data["scope"] = scope or self.data.get("scope", "")
        self.data["stage"] = "dev"
        self.data["round"] = 1
        self.event("init", summary=summary, branch=branch)

    def handoff(self, to: str, note: str = "", by: str = "") -> None:
        if to not in ("dev", "qa", "ship"):
            raise StateError(f"unknown handoff target '{to}' (dev|qa|ship)")
        if to == "dev" and self.data.get("stage") == "qa":
            self.data["round"] = int(self.data.get("round", 1)) + 1
        self.data["stage"] = to
        self.data["handoffs"].append(
            {"at": now(), "to": to, "by": by or "unknown", "round": self.data["round"], "note": note}
        )
        if to == "dev":
            self.data["verdict"] = None
        self.event("handoff", to=to, note=note, round=self.data["round"])

    def add_finding(self, **kw) -> str:
        severity = (kw.get("severity") or "medium").lower()
        if severity not in SEVERITIES:
            raise StateError(f"severity must be one of {', '.join(SEVERITIES)}")
        fid = f"F{len(self.data['findings']) + 1:03d}"
        finding = {
            "id": fid,
            "at": now(),
            "round": self.data.get("round", 1),
            "severity": severity,
            "status": "open",
            "title": kw.get("title", ""),
            "where": kw.get("where", ""),
            "repro": kw.get("repro", ""),
            "expected": kw.get("expected", ""),
            "actual": kw.get("actual", ""),
            "artifact": kw.get("artifact", ""),
            "notes": [],
        }
        self.data["findings"].append(finding)
        self.data["verdict"] = "FAIL"
        self.event("finding", id=fid, severity=severity, title=finding["title"])
        return fid

    def resolve_finding(self, fid: str, note: str = "", reject: bool = False) -> dict:
        for finding in self.data["findings"]:
            if finding["id"].lower() == fid.lower():
                finding["status"] = "rejected" if reject else "fixed"
                finding["notes"].append({"at": now(), "note": note})
                self.event("resolve", id=finding["id"], status=finding["status"], note=note)
                return finding
        raise StateError(f"no finding '{fid}' in {self.ticket}")

    def open_findings(self, min_severity: str = None) -> list:
        order = {s: i for i, s in enumerate(SEVERITIES)}
        cutoff = order.get((min_severity or "low").lower(), len(SEVERITIES) - 1)
        return [
            f for f in self.data["findings"]
            if f["status"] == "open" and order.get(f["severity"], 99) <= cutoff
        ]

    def record_tests(self, **kw) -> None:
        self.data["test_runs"].append({"at": now(), "round": self.data.get("round", 1), **kw})
        self.event("tests", **kw)

    def set_verdict(self, passed: bool, note: str = "") -> None:
        blocking = self.open_findings("high")
        if passed and blocking:
            raise StateError(
                "cannot record PASS while high/blocker findings are open: "
                + ", ".join(f["id"] for f in blocking)
            )
        self.data["verdict"] = "PASS" if passed else "FAIL"
        self.data["verdict_note"] = note
        self.data["stage"] = "ship" if passed else "dev"
        self.event("verdict", verdict=self.data["verdict"], note=note)

    def ship(self, mr_url: str, mr_iid: str = "", commit: str = "", jira_status: str = "") -> None:
        self.data["delivery"] = {
            "at": now(), "mr_url": mr_url, "mr_iid": mr_iid,
            "commit": commit, "jira_status": jira_status,
        }
        self.data["stage"] = "done"
        self.event("ship", mr_url=mr_url, mr_iid=mr_iid, commit=commit)

    def block(self, reason: str) -> None:
        self.data["stage"] = "blocked"
        self.data["blocked_reason"] = reason
        self.event("blocked", reason=reason)

    # ------------------------------------------------------------------ render

    def summary_text(self) -> str:
        d = self.data
        counts = {s: 0 for s in SEVERITIES}
        for f in d["findings"]:
            if f["status"] == "open":
                counts[f["severity"]] = counts.get(f["severity"], 0) + 1
        open_total = sum(counts.values())
        lines = [
            f"{d['ticket']} - {d.get('summary') or '(no summary)'}",
            f"  stage    {d.get('stage')}   round {d.get('round')}   verdict {d.get('verdict') or '-'}",
            f"  branch   {d.get('branch') or '-'}",
            f"  findings {len(d['findings'])} total, {open_total} open"
            + (f" ({', '.join(f'{k}:{v}' for k, v in counts.items() if v)})" if open_total else ""),
        ]
        if d.get("scope"):
            lines += ["", "  scope:"] + [f"    {ln}" for ln in d["scope"].strip().split("\n")]
        if d["findings"]:
            lines += ["", "  findings:"]
            for f in d["findings"]:
                mark = {"open": "[ ]", "fixed": "[x]", "rejected": "[-]"}.get(f["status"], "[?]")
                lines.append(f"    {mark} {f['id']} ({f['severity']}) {f['title']}")
                if f["status"] == "open":
                    if f.get("where"):
                        lines.append(f"          where:    {f['where']}")
                    if f.get("repro"):
                        lines.append(f"          repro:    {f['repro']}")
                    if f.get("expected"):
                        lines.append(f"          expected: {f['expected']}")
                    if f.get("actual"):
                        lines.append(f"          actual:   {f['actual']}")
                    if f.get("artifact"):
                        lines.append(f"          artifact: {f['artifact']}")
        if d["test_runs"]:
            lines += ["", "  test runs:"]
            for run in d["test_runs"][-6:]:
                lines.append(
                    f"    r{run.get('round')} {run.get('runner', '?'):10} "
                    f"passed={run.get('passed', '?')} failed={run.get('failed', '?')} "
                    f"skipped={run.get('skipped', '?')}"
                )
        if d["handoffs"]:
            lines += ["", "  handoffs:"]
            for h in d["handoffs"][-8:]:
                lines.append(f"    r{h['round']} -> {h['to']:5} {h.get('note', '')[:80]}")
        if d.get("delivery"):
            dv = d["delivery"]
            lines += ["", f"  MR       {dv.get('mr_url', '-')}", f"  commit   {dv.get('commit', '-')}"]
        if d.get("blocked_reason"):
            lines += ["", f"  BLOCKED  {d['blocked_reason']}"]
        return "\n".join(lines)

    def findings_markdown(self, only_open: bool = True) -> str:
        rows = [f for f in self.data["findings"] if not only_open or f["status"] == "open"]
        if not rows:
            return "_No open findings._"
        out = []
        for f in rows:
            out.append(f"### {f['id']} - {f['title']}  `{f['severity']}`")
            for label, key in (
                ("Where", "where"), ("Reproduce", "repro"),
                ("Expected", "expected"), ("Actual", "actual"), ("Artifact", "artifact"),
            ):
                if f.get(key):
                    out.append(f"- **{label}:** {f[key]}")
            out.append("")
        return "\n".join(out)


def state_root(cfg) -> Path:
    return Path.cwd() / str(cfg.get("workflow.state_dir", ".coddies"))
