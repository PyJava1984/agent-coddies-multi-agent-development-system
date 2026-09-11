"""Jira REST client for agent-coddies.

Supports Jira Cloud (email + API token, REST v3, ADF comment bodies) and
Jira Server/Data Center (personal access token, REST v2, wiki markup).
"""

from __future__ import annotations

import base64
import re
from typing import Any

from .config import Config
from .http import request


class JiraError(RuntimeError):
    pass


class Jira:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.base = str(cfg.require("jira.base_url")).rstrip("/")
        self.deployment = str(cfg.get("jira.deployment", "cloud")).lower()
        self.verify = cfg.get("jira.verify_ssl", True)
        token = str(cfg.require("jira.token"))
        email = cfg.get("jira.email", "")
        if self.deployment == "cloud":
            if not email:
                raise JiraError("jira.email is required for a Cloud deployment")
            raw = f"{email}:{token}".encode("utf-8")
            self.auth_header = "Basic " + base64.b64encode(raw).decode("ascii")
            self.api = f"{self.base}/rest/api/3"
        else:
            self.auth_header = f"Bearer {token}"
            self.api = f"{self.base}/rest/api/2"

    # ------------------------------------------------------------------ core

    def _call(self, method: str, path: str, **kw) -> Any:
        url = path if path.startswith("http") else f"{self.api}{path}"
        headers = dict(kw.pop("headers", {}))
        headers["Authorization"] = self.auth_header
        resp = request(method, url, headers=headers, verify=self.verify, **kw)
        if not resp.body:
            return None
        try:
            return resp.json()
        except Exception:  # noqa: BLE001 - some endpoints return empty/HTML
            return resp.text

    def whoami(self) -> dict:
        return self._call("GET", "/myself")

    # ------------------------------------------------------------------ issues

    def get_issue(self, key: str, fields: list = None) -> dict:
        params = {}
        if fields:
            params["fields"] = ",".join(fields)
        return self._call("GET", f"/issue/{key}", params=params)

    def search(self, jql: str, limit: int = 20, fields: list = None) -> dict:
        body = {"jql": jql, "maxResults": limit}
        if fields:
            body["fields"] = fields
        return self._call("POST", "/search", json_body=body)

    def comments(self, key: str, limit: int = 20) -> list:
        data = self._call("GET", f"/issue/{key}/comment", params={"maxResults": limit})
        return (data or {}).get("comments", [])

    def add_comment(self, key: str, markdown: str) -> dict:
        if self.deployment == "cloud":
            body = {"body": markdown_to_adf(markdown)}
        else:
            body = {"body": markdown_to_wiki(markdown)}
        return self._call("POST", f"/issue/{key}/comment", json_body=body)

    def transitions(self, key: str) -> list:
        data = self._call("GET", f"/issue/{key}/transitions", params={"expand": "transitions.fields"})
        return (data or {}).get("transitions", [])

    def transition(self, key: str, to_status: str) -> str:
        available = self.transitions(key)
        wanted = to_status.strip().lower()
        for tr in available:
            names = {
                str(tr.get("name", "")).lower(),
                str((tr.get("to") or {}).get("name", "")).lower(),
            }
            if wanted in names:
                self._call("POST", f"/issue/{key}/transitions", json_body={"transition": {"id": tr["id"]}})
                return tr.get("name", to_status)
        options = ", ".join(
            f"{t.get('name')} -> {(t.get('to') or {}).get('name')}" for t in available
        ) or "(none available)"
        raise JiraError(
            f"transition to '{to_status}' is not available on {key}. Available: {options}"
        )

    def add_remote_link(self, key: str, url: str, title: str, summary: str = "") -> dict:
        body = {
            "globalId": f"coddies-{url}",
            "object": {"url": url, "title": title, "summary": summary,
                       "icon": {"title": "GitLab", "url16x16": "https://gitlab.com/favicon.ico"}},
        }
        return self._call("POST", f"/issue/{key}/remotelink", json_body=body)

    def attachments(self, key: str) -> list:
        issue = self.get_issue(key, ["attachment"])
        return ((issue or {}).get("fields") or {}).get("attachment") or []


# --------------------------------------------------------------------- render


def _field(issue: dict, name: str, default: str = "") -> str:
    value = (issue.get("fields") or {}).get(name)
    if value is None:
        return default
    if isinstance(value, dict):
        return str(value.get("name") or value.get("displayName") or value.get("value") or default)
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, dict):
                parts.append(str(item.get("name") or item.get("value") or ""))
            else:
                parts.append(str(item))
        return ", ".join(p for p in parts if p)
    return str(value)


def adf_to_text(node: Any) -> str:
    """Flatten an Atlassian Document Format tree into readable markdown-ish text."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(adf_to_text(n) for n in node)
    ntype = node.get("type")
    content = node.get("content", [])
    if ntype == "text":
        text = node.get("text", "")
        for mark in node.get("marks", []) or []:
            kind = mark.get("type")
            if kind == "strong":
                text = f"**{text}**"
            elif kind == "em":
                text = f"*{text}*"
            elif kind == "code":
                text = f"`{text}`"
            elif kind == "link":
                href = (mark.get("attrs") or {}).get("href", "")
                text = f"[{text}]({href})"
        return text
    if ntype == "hardBreak":
        return "\n"
    if ntype == "paragraph":
        return adf_to_text(content) + "\n\n"
    if ntype == "heading":
        level = (node.get("attrs") or {}).get("level", 1)
        return "#" * int(level) + " " + adf_to_text(content) + "\n\n"
    if ntype in ("bulletList", "orderedList"):
        out = []
        for i, item in enumerate(content, 1):
            bullet = "- " if ntype == "bulletList" else f"{i}. "
            out.append(bullet + adf_to_text(item).strip() + "\n")
        return "".join(out) + "\n"
    if ntype == "listItem":
        return adf_to_text(content)
    if ntype == "codeBlock":
        lang = (node.get("attrs") or {}).get("language", "")
        return f"```{lang}\n{adf_to_text(content)}\n```\n\n"
    if ntype == "blockquote":
        inner = adf_to_text(content).strip()
        return "\n".join("> " + line for line in inner.split("\n")) + "\n\n"
    if ntype == "rule":
        return "---\n\n"
    if ntype in ("table", "tableRow", "tableCell", "tableHeader"):
        return adf_to_text(content)
    if ntype == "mediaSingle" or ntype == "media":
        return "[attachment]\n"
    return adf_to_text(content)


def issue_to_markdown(issue: dict, base_url: str = "") -> str:
    key = issue.get("key", "?")
    fields = issue.get("fields") or {}
    desc = fields.get("description")
    if isinstance(desc, dict):
        body = adf_to_text(desc)
    else:
        body = str(desc or "")

    lines = [f"# {key} - {_field(issue, 'summary', '(no summary)')}", ""]
    if base_url:
        lines.append(f"{base_url.rstrip('/')}/browse/{key}")
        lines.append("")
    meta = [
        ("Type", _field(issue, "issuetype")),
        ("Status", _field(issue, "status")),
        ("Priority", _field(issue, "priority")),
        ("Assignee", _field(issue, "assignee", "unassigned")),
        ("Labels", _field(issue, "labels")),
        ("Components", _field(issue, "components")),
        ("Fix versions", _field(issue, "fixVersions")),
    ]
    for label, value in meta:
        if value:
            lines.append(f"- **{label}:** {value}")
    lines.append("")
    lines.append("## Description")
    lines.append("")
    lines.append(body.strip() or "_(empty)_")

    links = fields.get("issuelinks") or []
    if links:
        lines += ["", "## Linked issues", ""]
        for link in links:
            other = link.get("outwardIssue") or link.get("inwardIssue") or {}
            rel = (link.get("type") or {}).get("outward" if link.get("outwardIssue") else "inward", "relates to")
            lines.append(
                f"- {rel} **{other.get('key', '?')}** - "
                f"{((other.get('fields') or {}).get('summary') or '')}"
            )

    subtasks = fields.get("subtasks") or []
    if subtasks:
        lines += ["", "## Subtasks", ""]
        for st in subtasks:
            status = ((st.get("fields") or {}).get("status") or {}).get("name", "")
            lines.append(
                f"- **{st.get('key')}** [{status}] "
                f"{((st.get('fields') or {}).get('summary') or '')}"
            )

    attach = fields.get("attachment") or []
    if attach:
        lines += ["", "## Attachments", ""]
        for att in attach:
            lines.append(f"- {att.get('filename')} ({att.get('size', 0)} bytes)")

    return "\n".join(lines).rstrip() + "\n"


# --------------------------------------------------------------------- markdown -> jira


_INLINE_CODE = re.compile(r"`([^`]+)`")
_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def _adf_inline(text: str) -> list:
    """Turn a line of markdown into ADF inline nodes (links, bold, code)."""
    nodes = []
    pos = 0
    pattern = re.compile(r"\[([^\]]+)\]\(([^)]+)\)|\*\*([^*]+)\*\*|`([^`]+)`")
    for m in pattern.finditer(text):
        if m.start() > pos:
            nodes.append({"type": "text", "text": text[pos:m.start()]})
        if m.group(1) is not None:
            nodes.append({
                "type": "text",
                "text": m.group(1),
                "marks": [{"type": "link", "attrs": {"href": m.group(2)}}],
            })
        elif m.group(3) is not None:
            nodes.append({"type": "text", "text": m.group(3), "marks": [{"type": "strong"}]})
        else:
            nodes.append({"type": "text", "text": m.group(4), "marks": [{"type": "code"}]})
        pos = m.end()
    if pos < len(text):
        nodes.append({"type": "text", "text": text[pos:]})
    return nodes or [{"type": "text", "text": ""}]


def markdown_to_adf(markdown: str) -> dict:
    """Convert the markdown subset used by our templates into ADF."""
    content = []
    lines = markdown.replace("\r\n", "\n").split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            i += 1
            continue

        if stripped.startswith("```"):
            lang = stripped[3:].strip()
            block, i = [], i + 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                block.append(lines[i])
                i += 1
            i += 1
            node = {"type": "codeBlock", "content": [{"type": "text", "text": "\n".join(block)}]}
            if lang:
                node["attrs"] = {"language": lang}
            content.append(node)
            continue

        if stripped.startswith("#"):
            level = len(stripped) - len(stripped.lstrip("#"))
            content.append({
                "type": "heading",
                "attrs": {"level": min(max(level, 1), 6)},
                "content": _adf_inline(stripped[level:].strip()),
            })
            i += 1
            continue

        if re.fullmatch(r"-{3,}|\*{3,}|_{3,}", stripped):
            content.append({"type": "rule"})
            i += 1
            continue

        if re.match(r"^([-*+]\s+|\d+[.)]\s+)", stripped):
            ordered = bool(re.match(r"^\d+[.)]\s+", stripped))
            items = []
            while i < len(lines) and re.match(r"^\s*([-*+]\s+|\d+[.)]\s+)", lines[i]):
                text = re.sub(r"^\s*([-*+]\s+|\d+[.)]\s+)", "", lines[i])
                items.append({
                    "type": "listItem",
                    "content": [{"type": "paragraph", "content": _adf_inline(text)}],
                })
                i += 1
            content.append({
                "type": "orderedList" if ordered else "bulletList",
                "content": items,
            })
            continue

        if stripped.startswith(">"):
            quote = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                quote.append(lines[i].strip().lstrip(">").strip())
                i += 1
            content.append({
                "type": "blockquote",
                "content": [{"type": "paragraph", "content": _adf_inline(" ".join(quote))}],
            })
            continue

        para = []
        while i < len(lines) and lines[i].strip() and not re.match(
            r"^\s*(#|```|>|[-*+]\s|\d+[.)]\s)", lines[i]
        ):
            para.append(lines[i].strip())
            i += 1
        content.append({"type": "paragraph", "content": _adf_inline(" ".join(para))})

    if not content:
        content = [{"type": "paragraph", "content": [{"type": "text", "text": ""}]}]
    return {"type": "doc", "version": 1, "content": content}


def markdown_to_wiki(markdown: str) -> str:
    """Convert the same markdown subset into Jira Server wiki markup."""
    out = []
    in_code = False
    for line in markdown.replace("\r\n", "\n").split("\n"):
        stripped = line.strip()
        if stripped.startswith("```"):
            if in_code:
                out.append("{code}")
                in_code = False
            else:
                lang = stripped[3:].strip()
                out.append("{code:%s}" % lang if lang else "{code}")
                in_code = True
            continue
        if in_code:
            out.append(line)
            continue
        if stripped.startswith("#"):
            level = len(stripped) - len(stripped.lstrip("#"))
            out.append(f"h{min(level, 6)}. {stripped[level:].strip()}")
            continue
        line = _LINK.sub(lambda m: f"[{m.group(1)}|{m.group(2)}]", line)
        line = _BOLD.sub(lambda m: f"*{m.group(1)}*", line)
        line = _INLINE_CODE.sub(lambda m: "{{%s}}" % m.group(1), line)
        line = re.sub(r"^(\s*)[-*+]\s+", lambda m: "*" * (len(m.group(1)) // 2 + 1) + " ", line)
        line = re.sub(r"^(\s*)\d+[.)]\s+", lambda m: "#" * (len(m.group(1)) // 2 + 1) + " ", line)
        out.append(line)
    if in_code:
        out.append("{code}")
    return "\n".join(out)
