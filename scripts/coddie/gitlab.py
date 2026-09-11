"""GitLab REST v4 client for agent-coddies.

Deliberately narrow: it can open and inspect merge requests and read pipelines.
It cannot merge, approve, delete a branch, or change project settings - those
stay with a human.
"""

from __future__ import annotations

import urllib.parse
from typing import Any

from .config import Config
from .http import request


class GitLabError(RuntimeError):
    pass


class GitLab:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.base = str(cfg.require("gitlab.base_url")).rstrip("/")
        self.token = str(cfg.require("gitlab.token"))
        self.verify = cfg.get("gitlab.verify_ssl", True)
        project = str(cfg.require("gitlab.project"))
        self.project_id = project if project.isdigit() else urllib.parse.quote(project, safe="")
        self.api = f"{self.base}/api/v4"

    def _call(self, method: str, path: str, **kw) -> Any:
        url = path if path.startswith("http") else f"{self.api}{path}"
        headers = dict(kw.pop("headers", {}))
        headers["PRIVATE-TOKEN"] = self.token
        resp = request(method, url, headers=headers, verify=self.verify, **kw)
        if not resp.body:
            return None
        try:
            return resp.json()
        except Exception:  # noqa: BLE001
            return resp.text

    def _project(self, path: str) -> str:
        return f"/projects/{self.project_id}{path}"

    # ------------------------------------------------------------------ info

    def whoami(self) -> dict:
        return self._call("GET", "/user")

    def project(self) -> dict:
        return self._call("GET", self._project(""))

    def branch_exists(self, name: str) -> bool:
        try:
            self._call("GET", self._project(f"/repository/branches/{urllib.parse.quote(name, safe='')}"))
            return True
        except Exception:  # noqa: BLE001 - 404 means absent
            return False

    # ------------------------------------------------------------------ merge requests

    def create_mr(
        self,
        source: str,
        target: str,
        title: str,
        description: str,
        labels: list = None,
        assignee_ids: list = None,
        reviewer_ids: list = None,
        remove_source_branch: bool = True,
        squash: bool = True,
        draft: bool = False,
    ) -> dict:
        existing = self.find_mr(source, target)
        if existing:
            raise GitLabError(
                f"an open merge request already exists for {source} -> {target}: "
                f"!{existing['iid']} {existing.get('web_url', '')}\n"
                f"update it with 'gitlab mr-update --iid {existing['iid']}' instead"
            )
        body = {
            "source_branch": source,
            "target_branch": target,
            "title": ("Draft: " + title) if draft and not title.lower().startswith("draft") else title,
            "description": description,
            "remove_source_branch": bool(remove_source_branch),
            "squash": bool(squash),
        }
        if labels:
            body["labels"] = ",".join(labels)
        if assignee_ids:
            body["assignee_ids"] = assignee_ids
        if reviewer_ids:
            body["reviewer_ids"] = reviewer_ids
        return self._call("POST", self._project("/merge_requests"), json_body=body)

    def update_mr(self, iid: int, **fields) -> dict:
        body = {k: v for k, v in fields.items() if v is not None}
        if "labels" in body and isinstance(body["labels"], list):
            body["labels"] = ",".join(body["labels"])
        return self._call("PUT", self._project(f"/merge_requests/{iid}"), json_body=body)

    def get_mr(self, iid: int) -> dict:
        return self._call("GET", self._project(f"/merge_requests/{iid}"))

    def find_mr(self, source: str, target: str = None) -> dict:
        params = {"source_branch": source, "state": "opened"}
        if target:
            params["target_branch"] = target
        found = self._call("GET", self._project("/merge_requests"), params=params)
        return (found or [None])[0] if found else None

    def mr_note(self, iid: int, body: str) -> dict:
        return self._call(
            "POST", self._project(f"/merge_requests/{iid}/notes"), json_body={"body": body}
        )

    def mr_changes(self, iid: int) -> dict:
        return self._call("GET", self._project(f"/merge_requests/{iid}/changes"))

    def mr_discussions(self, iid: int) -> list:
        return self._call(
            "GET", self._project(f"/merge_requests/{iid}/discussions"), params={"per_page": 100}
        ) or []

    # ------------------------------------------------------------------ pipelines

    def mr_pipelines(self, iid: int) -> list:
        return self._call("GET", self._project(f"/merge_requests/{iid}/pipelines")) or []

    def pipeline(self, pipeline_id: int) -> dict:
        return self._call("GET", self._project(f"/pipelines/{pipeline_id}"))

    def pipeline_jobs(self, pipeline_id: int) -> list:
        return self._call(
            "GET", self._project(f"/pipelines/{pipeline_id}/jobs"), params={"per_page": 100}
        ) or []

    def job_trace(self, job_id: int, tail_lines: int = 200) -> str:
        url = f"{self.api}{self._project(f'/jobs/{job_id}/trace')}"
        resp = request("GET", url, headers={"PRIVATE-TOKEN": self.token}, verify=self.verify)
        lines = resp.text.splitlines()
        return "\n".join(lines[-tail_lines:])

    # ------------------------------------------------------------------ users

    def find_user(self, username: str) -> dict:
        users = self._call("GET", "/users", params={"username": username})
        if not users:
            raise GitLabError(f"no GitLab user with username '{username}'")
        return users[0]

    def resolve_users(self, usernames: list) -> list:
        ids = []
        for name in usernames or []:
            name = str(name).lstrip("@").strip()
            if not name:
                continue
            ids.append(self.find_user(name)["id"])
        return ids
