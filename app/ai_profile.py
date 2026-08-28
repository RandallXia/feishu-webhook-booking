"""Static TOML profile parsing for the AI extraction pipeline.

Parses a profile TOML describing:
- [extract]: summary_field (the extract-table field that receives the AI summary)
- [bill]: app_token + table_id for the bill bitable
- prompt_header: system prompt prefix passed to the AI extractor
- [[fields]]: ordered FieldSpec list (one target="extract", rest target="bill")

This module is intentionally stateless — no hot reload, no registry. Todo 8
wraps parse_profile in an AiProfileRegistry with mtime-based reload, mirroring
TargetRegistry's snapshot-swap pattern.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from .field_codec import FieldSpec


class ProfileConfigError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class AiProfile:
    prompt_header: str
    summary_field: str
    bill_app_token: str
    bill_table_id: str
    fields: tuple[FieldSpec, ...]


_VALID_TYPES = {"text", "number", "single_select", "date", "passthrough"}
_VALID_TARGETS = {"extract", "bill"}


def parse_profile(path: Path) -> AiProfile:
    if not path.is_file():
        raise ProfileConfigError(f"Missing profile file: {path}")

    data = tomllib.loads(path.read_text(encoding="utf-8"))

    extract_section = data.get("extract")
    if not isinstance(extract_section, dict):
        raise ProfileConfigError("profile must define [extract] section")
    summary_field = str(extract_section.get("summary_field", "")).strip()
    if not summary_field:
        raise ProfileConfigError("[extract].summary_field must be non-empty")

    bill_section = data.get("bill")
    if not isinstance(bill_section, dict):
        raise ProfileConfigError("profile must define [bill] section")
    bill_app_token = str(bill_section.get("app_token", "")).strip()
    if not bill_app_token:
        raise ProfileConfigError("[bill].app_token must be non-empty")
    bill_table_id = str(bill_section.get("table_id", "")).strip()
    if not bill_table_id:
        raise ProfileConfigError("[bill].table_id must be non-empty")

    prompt_header = str(data.get("prompt_header", "")).strip()
    if not prompt_header:
        raise ProfileConfigError("prompt_header must be non-empty")

    raw_fields = data.get("fields")
    if not isinstance(raw_fields, list) or not raw_fields:
        raise ProfileConfigError("profile must define at least one [[fields]] entry")

    seen_ai_keys: set[str] = set()
    specs: list[FieldSpec] = []
    extract_count = 0

    for idx, raw in enumerate(raw_fields):
        if not isinstance(raw, dict):
            raise ProfileConfigError(f"field #{idx} must be an object")

        ai_key = str(raw.get("ai_key", "")).strip()
        if not ai_key:
            raise ProfileConfigError(f"field #{idx} is missing ai_key")
        if ai_key in seen_ai_keys:
            raise ProfileConfigError(f"duplicate ai_key: {ai_key}")
        seen_ai_keys.add(ai_key)

        feishu_field = str(raw.get("feishu_field", "")).strip()
        if not feishu_field:
            raise ProfileConfigError(f"field {ai_key!r} is missing feishu_field")

        field_type = str(raw.get("type", "")).strip()
        if field_type not in _VALID_TYPES:
            raise ProfileConfigError(
                f"field {ai_key!r} has invalid type: {field_type!r}"
            )

        target = str(raw.get("target", "")).strip()
        if target not in _VALID_TARGETS:
            raise ProfileConfigError(
                f"field {ai_key!r} has invalid target: {target!r}"
            )

        fallback_raw = raw.get("fallback")
        fallback = str(fallback_raw).strip() if fallback_raw is not None else None

        if field_type == "single_select" and not fallback:
            raise ProfileConfigError(
                f"field {ai_key!r} is single_select and must have a fallback"
            )

        source_raw = raw.get("source")
        source = str(source_raw).strip() if source_raw is not None else None

        if field_type == "passthrough" and source != "summary":
            raise ProfileConfigError(
                f"field {ai_key!r} is passthrough and must have source=\"summary\""
            )

        if target == "extract":
            extract_count += 1

        specs.append(
            FieldSpec(
                ai_key=ai_key,
                feishu_field=feishu_field,
                type=field_type,
                target=target,
                fallback=fallback,
                prompt=str(raw.get("prompt", "")).strip(),
                source=source,
            )
        )

    if extract_count == 0:
        raise ProfileConfigError(
            "profile must have exactly one field with target=\"extract\""
        )
    if extract_count > 1:
        raise ProfileConfigError(
            "profile must have exactly one field with target=\"extract\", "
            f"found {extract_count}"
        )

    extract_spec = next(s for s in specs if s.target == "extract")
    if extract_spec.feishu_field != summary_field:
        raise ProfileConfigError(
            f"[extract].summary_field ({summary_field!r}) must equal the "
            f"feishu_field of the target=\"extract\" field "
            f"({extract_spec.feishu_field!r})"
        )

    return AiProfile(
        prompt_header=prompt_header,
        summary_field=summary_field,
        bill_app_token=bill_app_token,
        bill_table_id=bill_table_id,
        fields=tuple(specs),
    )
