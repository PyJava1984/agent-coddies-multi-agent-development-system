"""Configuration and credential loading for agent-coddies.

Resolution order for the config directory:
  1. $CODDIES_CONFIG_DIR
  2. ./.coddies/config           (per-repo override, if it has credentials.yaml)
  3. <skill_dir>/config

Values support two indirections so secrets need not sit on disk:
  ${ENV_VAR}        -> os.environ["ENV_VAR"]  (${VAR:-default} also works)
  file:/path/secret -> the stripped contents of that file
"""

from __future__ import annotations

import os
import re
import stat
import sys
from pathlib import Path
from typing import Any

try:  # PyYAML when available, minimal reader otherwise.
    import yaml as _yaml

    def _parse_yaml(text: str) -> Any:
        return _yaml.safe_load(text) or {}

    YAML_BACKEND = "pyyaml"
except ImportError:  # pragma: no cover - environment dependent
    from . import yamlmin as _yamlmin

    def _parse_yaml(text: str) -> Any:
        return _yamlmin.loads(text)

    YAML_BACKEND = "builtin"


SECRET_KEY_HINTS = (
    "token", "password", "passwd", "secret", "api_key", "apikey",
    "private_key", "cookie", "authorization", "credential",
)

_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


class ConfigError(RuntimeError):
    """Raised for a missing, malformed, or incomplete configuration."""


# --------------------------------------------------------------------------- paths


def skill_dir() -> Path:
    """The agent-coddies root (the directory containing SKILL.md)."""
    return Path(__file__).resolve().parent.parent.parent


def config_dir() -> Path:
    env = os.environ.get("CODDIES_CONFIG_DIR")
    if env:
        return Path(env).expanduser().resolve()
    local = Path.cwd() / ".coddies" / "config"
    if (local / "credentials.yaml").exists():
        return local
    return skill_dir() / "config"


# --------------------------------------------------------------------------- loading


def _resolve_value(value: Any, path: str, problems: list) -> Any:
    if isinstance(value, dict):
        return {k: _resolve_value(v, f"{path}.{k}", problems) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_value(v, f"{path}[{i}]", problems) for i, v in enumerate(value)]
    if not isinstance(value, str):
        return value

    def sub(match):
        name, default = match.group(1), match.group(2)
        if name in os.environ:
            return os.environ[name]
        if default is not None:
            return default
        problems.append(f"{path.lstrip('.')}: environment variable {name} is not set")
        return ""

    out = _ENV_RE.sub(sub, value)
    if out.startswith("file:"):
        ref = Path(out[5:].strip()).expanduser()
        if not ref.is_file():
            problems.append(f"{path.lstrip('.')}: file reference not found: {ref}")
            return ""
        return ref.read_text(encoding="utf-8").strip()
    return out


def _read_yaml(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        data = _parse_yaml(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:  # noqa: BLE001 - surfaced to the user verbatim
        hint = ""
        if YAML_BACKEND == "builtin":
            hint = "  (install PyYAML for full YAML support: pip install pyyaml)"
        raise ConfigError(f"{path}: could not parse YAML - {exc}{hint}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: expected a mapping at the top level")
    return data


def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for key, val in over.items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], val)
        else:
            out[key] = val
    return out


def _warn_permissions(path: Path) -> None:
    if os.name == "nt" or not path.exists():
        return
    mode = path.stat().st_mode
    if mode & (stat.S_IRGRP | stat.S_IROTH):
        print(f"warning: {path} is readable by other users - chmod 600 it", file=sys.stderr)


class Config:
    """Merged behaviour config + credentials, with redaction helpers."""

    def __init__(self, data: dict, sources: dict, problems: list):
        self._data = data
        self.sources = sources
        self.problems = problems

    # ---- access

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self._data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return default if node is None else node

    def require(self, dotted: str) -> Any:
        value = self.get(dotted)
        if value in (None, "", []):
            target = self.sources.get("credentials", config_dir() / "credentials.yaml")
            raise ConfigError(
                f"missing required config value '{dotted}' - set it in {target}"
            )
        return value

    def section(self, name: str) -> dict:
        value = self.get(name, {})
        return value if isinstance(value, dict) else {}

    @property
    def raw(self) -> dict:
        return self._data

    # ---- redaction

    @staticmethod
    def mask(value: Any) -> str:
        if value in (None, ""):
            return "(unset)"
        text = str(value)
        if len(text) <= 8:
            return "*" * len(text)
        return f"{text[:3]}...{text[-2:]} ({len(text)} chars)"

    def _secret_values(self) -> list:
        found = []

        def collect(node: Any, key: str = "") -> None:
            if isinstance(node, dict):
                for k, v in node.items():
                    collect(v, k)
            elif isinstance(node, list):
                for v in node:
                    collect(v, key)
            elif isinstance(node, str) and len(node) >= 8:
                if any(hint in key.lower() for hint in SECRET_KEY_HINTS):
                    found.append(node)

        collect(self._data)
        return found

    def redacted(self) -> dict:
        def walk(node: Any, key: str = "") -> Any:
            if isinstance(node, dict):
                return {k: walk(v, k) for k, v in node.items()}
            if isinstance(node, list):
                return [walk(v, key) for v in node]
            if any(hint in key.lower() for hint in SECRET_KEY_HINTS):
                return self.mask(node)
            return node

        return walk(self._data)

    def scrub(self, text: str) -> str:
        """Replace any known secret value appearing in *text* with a marker."""
        for secret in sorted(set(self._secret_values()), key=len, reverse=True):
            text = text.replace(secret, "***REDACTED***")
        return text


_CACHE = None


def load(strict: bool = True, reload: bool = False) -> Config:
    """Load config.yaml + credentials.yaml (example files as fallback)."""
    global _CACHE
    if _CACHE is not None and not reload:
        return _CACHE

    cdir = config_dir()
    sources = {}
    problems = []

    behaviour_path = cdir / "config.yaml"
    if not behaviour_path.is_file():
        behaviour_path = cdir / "config.example.yaml"
    behaviour = _read_yaml(behaviour_path)
    if behaviour:
        sources["config"] = behaviour_path

    creds_path = cdir / "credentials.yaml"
    if not creds_path.is_file():
        if strict:
            example = cdir / "credentials.example.yaml"
            raise ConfigError(
                f"no credentials file at {creds_path}\n"
                f'  create it:  cp "{example}" "{creds_path}"\n'
                f"  then fill in jira.*, gitlab.* and (optionally) database.*"
            )
        creds_path = cdir / "credentials.example.yaml"
    _warn_permissions(creds_path)
    creds = _read_yaml(creds_path)
    if creds:
        sources["credentials"] = creds_path

    merged = _deep_merge(behaviour, creds)
    resolved = _resolve_value(merged, "", problems)
    _CACHE = Config(resolved, sources, problems)
    return _CACHE
