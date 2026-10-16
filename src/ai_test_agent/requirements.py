"""M1 source ingestion and deterministic requirement normalization.

This module only validates and fingerprints source material.  It never sends
source text to a model and does not interpret the source as executable input.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any


class M1InputError(ValueError):
    """Raised when an M1 source document or JSON file is not admissible."""


_PERMISSIONS = {"user-approved", "unverified"}
_DATA_CLASSES = {"synthetic", "user-contract", "project-document"}
_REQUIRED_SOURCE_KEYS = {
    "source_id",
    "source_version",
    "source_ref",
    "permission",
    "sections",
}
_OPTIONAL_SOURCE_KEYS = {"model_access", "data_class", "content_sha256"}
_SECTION_KEYS = {"section_id", "title", "text", "locator", "section_sha256"}


def _nonempty_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise M1InputError(f"{field} must be a nonempty string")
    # Validation may inspect whitespace, but source text and metadata are kept
    # byte-for-byte as supplied by the caller.
    return value


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _reject_unknown_keys(value: dict[str, Any], allowed: set[str], field: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise M1InputError(f"{field} contains unknown field(s): {', '.join(unknown)}")


def normalize_source(source: dict) -> dict:
    """Validate and fingerprint an M1 source document.

    All input strings are preserved exactly, including leading/trailing
    whitespace.  Whitespace is inspected only to reject empty values.
    """

    if not isinstance(source, dict):
        raise M1InputError("source must be an object")
    _reject_unknown_keys(source, _REQUIRED_SOURCE_KEYS | _OPTIONAL_SOURCE_KEYS, "source")

    normalized: dict[str, Any] = {
        "source_id": _nonempty_string(source.get("source_id"), "source_id"),
        "source_version": _nonempty_string(source.get("source_version"), "source_version"),
        "source_ref": _nonempty_string(source.get("source_ref"), "source_ref"),
        "permission": _nonempty_string(source.get("permission"), "permission"),
        "model_access": source.get("model_access", False),
        "data_class": source.get("data_class", "project-document"),
        "sections": [],
    }

    if normalized["permission"] not in _PERMISSIONS:
        raise M1InputError("permission must be user-approved or unverified")
    if type(normalized["model_access"]) is not bool:
        raise M1InputError("model_access must be a boolean")
    if not isinstance(normalized["data_class"], str) or normalized["data_class"] not in _DATA_CLASSES:
        raise M1InputError("data_class must be synthetic, user-contract or project-document")

    sections = source.get("sections")
    if not isinstance(sections, list) or not sections:
        raise M1InputError("sections must be a nonempty list")

    section_ids: set[str] = set()
    for index, raw_section in enumerate(sections):
        if not isinstance(raw_section, dict):
            raise M1InputError(f"sections[{index}] must be an object")
        _reject_unknown_keys(raw_section, _SECTION_KEYS, f"sections[{index}]")
        section_id = _nonempty_string(raw_section.get("section_id"), f"sections[{index}].section_id")
        if section_id in section_ids:
            raise M1InputError(f"duplicate section_id: {section_id}")
        section_ids.add(section_id)

        section = {
            "section_id": section_id,
            "title": _nonempty_string(raw_section.get("title"), f"sections[{index}].title"),
            "text": _nonempty_string(raw_section.get("text"), f"sections[{index}].text"),
            "locator": _nonempty_string(raw_section.get("locator"), f"sections[{index}].locator"),
        }
        section_digest = _sha256_text(section["text"])
        supplied_section_digest = raw_section.get("section_sha256")
        if "section_sha256" in raw_section and supplied_section_digest != section_digest:
            raise M1InputError(f"sections[{index}].section_sha256 does not match text")
        section["section_sha256"] = section_digest
        normalized["sections"].append(section)

    # The digest covers the normalized source without the digest field itself.
    # This avoids a recursive hash while covering every source property and
    # each section's text fingerprint.
    content_digest = _sha256_text(_canonical_json(normalized).decode("utf-8"))
    supplied_content_digest = source.get("content_sha256")
    if "content_sha256" in source and supplied_content_digest != content_digest:
        raise M1InputError("content_sha256 does not match normalized source")
    normalized["content_sha256"] = content_digest
    return normalized


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise M1InputError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> Any:
    raise M1InputError(f"non-finite JSON number is not allowed: {value}")


def _parse_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise M1InputError(f"non-finite JSON number is not allowed: {value}")
    return number


def read_json(path: str | Path) -> Any:
    """Read JSON while rejecting duplicate object keys and NaN/Infinity."""

    try:
        raw = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise M1InputError(f"unable to read JSON file: {path}") from error
    try:
        return json.loads(
            raw,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonfinite,
            parse_float=_parse_float,
        )
    except M1InputError:
        raise
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise M1InputError(f"invalid JSON: {path}") from error
