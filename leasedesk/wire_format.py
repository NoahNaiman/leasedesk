"""Translate the LeaseAbstract schema into a form the API's structured outputs can compile.

Structured outputs compile the JSON schema into a grammar, and the API caps how large
that grammar may be. A lease abstract has dozens of optional fields, and every optional
number, date, or yes/no (a "type or null" union) adds to the grammar. Together they
exceed the cap.

So on the wire, every optional scalar is a string, with "" meaning "not stated in the
document". ``from_wire`` converts the reply back (numbers, booleans, None) before
Pydantic validates it against the real ``LeaseAbstract``. The rest of the app never
sees the wire format.
"""

from __future__ import annotations

import re

import anthropic

from leasedesk.models import LeaseAbstract

NOT_STATED = " Use an empty string if the document does not state it."
_HINTS = {
    "number": " A plain number with no $ signs, % signs, or commas.",
    "integer": " A whole number.",
}
_BLANK_WORDS = {"", "n/a", "na", "none", "null", "not stated", "unknown"}


def _optional_scalar(node: dict) -> dict | None:
    """The scalar variant of a ``scalar | null`` union, or None if the node isn't one."""
    variants = node.get("anyOf") if isinstance(node, dict) else None
    if not variants or len(variants) != 2:
        return None
    non_null = [v for v in variants if v.get("type") != "null"]
    if len(non_null) == 1 and non_null[0].get("type") in ("string", "number", "integer", "boolean"):
        return non_null[0]
    return None


def _to_wire(node):
    if isinstance(node, list):
        return [_to_wire(n) for n in node]
    if not isinstance(node, dict):
        return node
    scalar = _optional_scalar(node)
    if scalar is not None:
        description = node.get("description", "")
        if scalar.get("format") == "date":
            description += " Format YYYY-MM-DD."
        description += _HINTS.get(scalar["type"], "")
        out = {"type": "string"}
        if scalar["type"] == "boolean":
            out = {"enum": ["yes", "no", ""]}
        out["description"] = (description + NOT_STATED).strip()
        if "title" in node:
            out["title"] = node["title"]
        return out
    out = {k: _to_wire(v) for k, v in node.items() if k != "default"}
    if out.get("type") == "object" and "properties" in out:
        # Every key is always present, which also keeps the grammar small.
        out["required"] = list(out["properties"])
    return out


def _parse_scalar(value, kind: str):
    if not isinstance(value, str):
        return value
    text = value.strip()
    if text.lower() in _BLANK_WORDS:
        return None
    if kind == "boolean":
        return {"yes": True, "no": False}.get(text.lower())
    if kind in ("number", "integer"):
        try:
            number = float(re.sub(r"[$,%\s]", "", text))
        except ValueError:
            return None  # left blank for the reviewer rather than failing the whole lease
        return round(number) if kind == "integer" else number
    return text


def _from_wire(data, node: dict, defs: dict):
    if "$ref" in node:
        node = defs[node["$ref"].split("/")[-1]]
    if data is None:
        return None
    scalar = _optional_scalar(node)
    if scalar is not None:
        return _parse_scalar(data, scalar["type"])
    if "anyOf" in node:
        non_null = [v for v in node["anyOf"] if v.get("type") != "null"]
        return _from_wire(data, non_null[0], defs) if len(non_null) == 1 else data
    if isinstance(data, dict):
        props = node.get("properties", {})
        return {k: _from_wire(v, props.get(k, {}), defs) for k, v in data.items()}
    if isinstance(data, list):
        return [_from_wire(v, node.get("items", {}), defs) for v in data]
    return data


_SCHEMA = LeaseAbstract.model_json_schema()
WIRE_SCHEMA = anthropic.transform_schema(_to_wire(_SCHEMA))


def from_wire(data: dict) -> LeaseAbstract:
    """Validate a reply that followed ``WIRE_SCHEMA`` as a ``LeaseAbstract``."""
    return LeaseAbstract.model_validate(_from_wire(data, _SCHEMA, _SCHEMA.get("$defs", {})))


def to_wire_json(abstract: LeaseAbstract) -> dict:
    """The reply Claude would send for this abstract. Used by tests and the fake API."""
    def convert(data, node):
        if "$ref" in node:
            node = _SCHEMA["$defs"][node["$ref"].split("/")[-1]]
        scalar = _optional_scalar(node)
        if scalar is not None:
            if data is None:
                return ""
            if isinstance(data, bool):
                return "yes" if data else "no"
            return str(data)
        if "anyOf" in node:
            non_null = [v for v in node["anyOf"] if v.get("type") != "null"]
            return None if data is None else convert(data, non_null[0])
        if isinstance(data, dict):
            return {k: convert(v, node.get("properties", {}).get(k, {})) for k, v in data.items()}
        if isinstance(data, list):
            return [convert(v, node.get("items", {})) for v in data]
        return data
    return convert(abstract.model_dump(mode="json"), _SCHEMA)
