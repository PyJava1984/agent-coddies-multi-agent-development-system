"""Minimal YAML reader used only when PyYAML is unavailable.

Supports the subset the agent-coddies config files use: nested mappings by
indentation, block sequences, inline ``[a, b]`` flow sequences, quoted and bare
scalars, ``|`` block scalars, comments and blank lines. Anything more exotic
(anchors, multi-document streams, flow mappings) raises, with a clear message
telling the user to install PyYAML.
"""

from __future__ import annotations

import re

_TRUE = {"true", "yes", "on"}
_FALSE = {"false", "no", "off"}
_NULL = {"", "null", "~", "none"}


class YamlError(ValueError):
    pass


def _scalar(raw: str):
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        return raw[1:-1]
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1].strip()
        if not inner:
            return []
        return [_scalar(part) for part in _split_flow(inner)]
    low = raw.lower()
    if low in _TRUE:
        return True
    if low in _FALSE:
        return False
    if low in _NULL:
        return None
    if re.fullmatch(r"[-+]?\d+", raw):
        return int(raw)
    if re.fullmatch(r"[-+]?\d*\.\d+", raw):
        return float(raw)
    return raw


def _split_flow(text: str):
    parts, buf, quote = [], [], None
    for ch in text:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
            buf.append(ch)
        elif ch == ",":
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if buf:
        parts.append("".join(buf))
    return [p.strip() for p in parts if p.strip()]


def _strip_comment(line: str) -> str:
    out, quote = [], None
    for i, ch in enumerate(line):
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
            out.append(ch)
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            break
        else:
            out.append(ch)
    return "".join(out).rstrip()


def _tokenize(text: str):
    """Yield (indent, content, lineno) for significant lines."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    i = 0
    while i < len(lines):
        raw = lines[i]
        stripped = _strip_comment(raw)
        if not stripped.strip():
            i += 1
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if "\t" in raw[:indent]:
            raise YamlError(f"line {i + 1}: tabs are not valid YAML indentation")
        content = stripped.strip()
        # Block scalar: `key: |` or `key: >`
        m = re.match(r"^(.*?):\s*([|>])[-+]?\s*$", content)
        if m:
            key, style = m.group(1), m.group(2)
            block, j = [], i + 1
            while j < len(lines):
                nxt = lines[j]
                if not nxt.strip():
                    block.append("")
                    j += 1
                    continue
                nxt_indent = len(nxt) - len(nxt.lstrip(" "))
                if nxt_indent <= indent:
                    break
                block.append(nxt)
                j += 1
            base = min(
                (len(b) - len(b.lstrip(" ")) for b in block if b.strip()),
                default=indent + 2,
            )
            body = "\n".join(b[base:] if b.strip() else "" for b in block)
            while body.endswith("\n\n"):
                body = body[:-1]
            if style == ">":
                body = " ".join(part.strip() for part in body.split("\n") if part.strip())
            elif not body.endswith("\n"):
                body += "\n"
            yield indent, (key.strip(), body), i + 1
            i = j
            continue
        yield indent, content, i + 1
        i += 1


def loads(text: str):
    tokens = list(_tokenize(text))
    value, idx = _parse_block(tokens, 0, tokens[0][0] if tokens else 0)
    if idx != len(tokens):
        raise YamlError(f"line {tokens[idx][2]}: unexpected indentation")
    return value if value is not None else {}


def _parse_block(tokens, idx, indent):
    if idx >= len(tokens):
        return None, idx
    content = tokens[idx][1]
    is_seq = isinstance(content, str) and (content == "-" or content.startswith("- "))
    if is_seq:
        return _parse_seq(tokens, idx, indent)
    return _parse_map(tokens, idx, indent)


def _parse_seq(tokens, idx, indent):
    items = []
    while idx < len(tokens):
        cur_indent, content, lineno = tokens[idx]
        if cur_indent < indent:
            break
        if cur_indent > indent:
            raise YamlError(f"line {lineno}: unexpected indentation in sequence")
        if not (isinstance(content, str) and (content == "-" or content.startswith("- "))):
            break
        rest = content[1:].strip()
        idx += 1
        if not rest:
            if idx < len(tokens) and tokens[idx][0] > indent:
                child, idx = _parse_block(tokens, idx, tokens[idx][0])
                items.append(child)
            else:
                items.append(None)
            continue
        if ":" in rest and not _looks_scalar(rest):
            # Inline mapping opening a sequence item: "- key: value"
            sub = [(indent + 2, rest, lineno)]
            j = idx
            while j < len(tokens) and tokens[j][0] > indent:
                sub.append(tokens[j])
                j += 1
            child, _ = _parse_map(sub, 0, indent + 2)
            items.append(child)
            idx = j
        else:
            items.append(_scalar(rest))
    return items, idx


def _looks_scalar(text: str) -> bool:
    if text[:1] in "\"'":
        return True
    key = text.split(":", 1)[0]
    return bool(re.search(r"[\s]", key.strip())) and " " in key.strip() and False


def _parse_map(tokens, idx, indent):
    result = {}
    while idx < len(tokens):
        cur_indent, content, lineno = tokens[idx]
        if cur_indent < indent:
            break
        if cur_indent > indent:
            raise YamlError(f"line {lineno}: unexpected indentation in mapping")
        if isinstance(content, tuple):  # block scalar
            result[content[0]] = content[1]
            idx += 1
            continue
        if content.startswith("- "):
            break
        if ":" not in content:
            raise YamlError(f"line {lineno}: expected 'key: value', got {content!r}")
        key, _, rest = content.partition(":")
        key = _scalar(key) if key.strip()[:1] in "\"'" else key.strip()
        rest = rest.strip()
        idx += 1
        if rest:
            result[key] = _scalar(rest)
            continue
        if idx < len(tokens) and tokens[idx][0] > indent:
            child, idx = _parse_block(tokens, idx, tokens[idx][0])
            result[key] = child
        elif idx < len(tokens) and tokens[idx][0] == indent and isinstance(
            tokens[idx][1], str
        ) and tokens[idx][1].startswith("- "):
            child, idx = _parse_seq(tokens, idx, indent)
            result[key] = child
        else:
            result[key] = None
    return result, idx
