"""Feishu field encoder — whitelist-guarded single_select, timezone-aware date, pure functions.

Converts an ExtractionResult into Feishu API field dicts using a list of FieldSpec
definitions. Key design decisions:

- **single_select**: value checked against a whitelist (option_whitelists). Unknown
  values trigger a fallback + warning, never pass through. This prevents Feishu
  auto-creating misspelled/dirty options (irreversible pollution).
- **date**: parsed as YYYY-MM-DD, then converted to Asia/Shanghai midnight ms
  timestamp. Bad formats fall back to today in Shanghai (not UTC).
- **passthrough**: copies a source field (e.g. extraction.summary) verbatim.
- **routing**: each spec targets either "extract" or "bill" dict — the caller
  merges into the appropriate Feishu API call.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from .ai_extractor import ExtractionResult


_SHANGHAI = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True, slots=True)
class FieldSpec:
    """One field mapping from AI extraction → Feishu bitable field.

    Attributes:
        ai_key: ExtractionResult attribute name (e.g. "category", "amount").
        feishu_field: Feishu bitable field name (e.g. "分类", "金额").
        type: Field type — "text", "number", "single_select", "date", "passthrough".
        target: Target dict — "extract" or "bill".
        fallback: Fallback value for single_select when value not in whitelist.
        prompt: Prompt description for this field (used by config, not encoding).
        source: Source field for passthrough type (e.g. "summary").
    """
    ai_key: str
    feishu_field: str
    type: str  # "text" | "number" | "single_select" | "date" | "passthrough"
    target: str  # "extract" | "bill"
    fallback: str | None = None
    prompt: str = ""
    source: str | None = None  # passthrough only, e.g. "summary"


def encode_fields(
    extraction: ExtractionResult,
    specs: list[FieldSpec],
    option_whitelists: dict[str, set[str]],
) -> tuple[dict[str, object], dict[str, object], list[str]]:
    """Encode an ExtractionResult into Feishu field dicts.

    Args:
        extraction: The structured extraction result.
        specs: Ordered list of field mappings.
        option_whitelists: feishu_field → set of allowed option values.
            Only used for single_select specs.

    Returns:
        (extract_fields, bill_fields, warnings):
            - extract_fields: dict for the extract table PUT.
            - bill_fields: dict for the bill table PUT.
            - warnings: human-readable strings for non-fatal encoding issues
              (empty text, option fallback, date fallback).
    """
    extract_fields: dict[str, object] = {}
    bill_fields: dict[str, object] = {}
    warnings: list[str] = []

    for spec in specs:
        field_name = spec.feishu_field
        value: object = None

        if spec.type == "text":
            raw = str(getattr(extraction, spec.ai_key)).strip()
            if not raw:
                warnings.append(f"empty text skipped: {field_name}")
                continue
            value = raw

        elif spec.type == "number":
            value = float(getattr(extraction, spec.ai_key))

        elif spec.type == "single_select":
            raw = str(getattr(extraction, spec.ai_key))
            whitelist = option_whitelists.get(field_name, set())
            if raw in whitelist:
                value = raw
            elif spec.fallback is not None and spec.fallback in whitelist:
                warnings.append(
                    f"option fallback: {field_name}: {raw!r} -> {spec.fallback!r}"
                )
                value = spec.fallback
            elif spec.fallback is not None:
                raise ValueError(
                    f"single_select fallback {spec.fallback!r} also not in "
                    f"whitelist for {field_name}"
                )
            else:
                raise ValueError(
                    f"single_select value {raw!r} not in whitelist for "
                    f"{field_name} and no fallback"
                )

        elif spec.type == "date":
            raw = str(getattr(extraction, spec.ai_key))
            try:
                parsed = datetime.strptime(raw, "%Y-%m-%d")
            except ValueError:
                warnings.append(f"date fallback to today: {field_name}: {raw!r}")
                today = datetime.now(_SHANGHAI)
                parsed = today.replace(hour=0, minute=0, second=0, microsecond=0)
            else:
                parsed = parsed.replace(tzinfo=_SHANGHAI)
            value = int(parsed.timestamp() * 1000)

        elif spec.type == "passthrough":
            if spec.source == "summary":
                value = extraction.summary
            else:
                continue

        else:
            continue

        target_dict = extract_fields if spec.target == "extract" else bill_fields
        target_dict[field_name] = value

    return extract_fields, bill_fields, warnings