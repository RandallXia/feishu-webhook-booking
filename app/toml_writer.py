"""Deterministic TOML serializer for config files.

Two pure functions (zero IO, zero dependencies):
- dump_targets: feishu-targets.toml
- dump_profile:  ai-profile.toml

Key design:
- Explicit key-order loops for deterministic output (no dict iteration drift).
- prompt_header always emitted before any [section] (TOML rule: bare key after
  a section header attaches to that section).
- None values are omitted; enabled defaults to True.
- Strings escaped per TOML basic-string rules: " → \\", \\ → \\\\, \\n → \\n.
"""

from __future__ import annotations

_TARGET_KEY_ORDER = (
    "app_token",
    "table_id",
    "record_id",
    "original_field_name",
    "year",
    "enabled",
)

_FIELD_KEY_ORDER = (
    "ai_key",
    "feishu_field",
    "type",
    "target",
    "fallback",
    "source",
    "prompt",
)


def _escape_str(s: str) -> str:
    """Wrap *s* in a TOML basic string, escaping special characters.

    Escapes: backslash, double-quote, newline.  Chinese / UTF-8 preserved as-is.
    """
    result = s.replace("\\", "\\\\")
    result = result.replace("\"", "\\\"")
    result = result.replace("\n", "\\n")
    return f'"{result}"'


def _format_value(val: object) -> str:
    """Format a single TOML value as a string.

    bool → lowercased ``true``/``false``; int → bare integer; str → escaped.
    """
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, int):
        return str(val)
    return _escape_str(str(val))


# ---------------------------------------------------------------------------
# dump_targets
# ---------------------------------------------------------------------------

def dump_targets(data: dict) -> str:
    """Serialize a targets dict to deterministic TOML.

    Input shape (mirrors feishu-targets.toml parsed by tomllib)::

        {
            "default_alias": str,
            "targets": {
                alias: {
                    "app_token": str,
                    "table_id": str,
                    "record_id": str,
                    "original_field_name": str,
                    "year": int | None,
                    "enabled": bool,       # omitted → output true
                },
            },
        }
    """
    lines: list[str] = []

    lines.append(f"default_alias = {_escape_str(data['default_alias'])}")
    lines.append("")

    targets = data.get("targets", {})
    for alias in targets:
        section = targets[alias]
        lines.append(f"[targets.{_escape_str(alias)}]")
        for key in _TARGET_KEY_ORDER:
            if key == "enabled":
                val = section.get("enabled", True)
            else:
                val = section.get(key)
            if val is None:
                continue
            lines.append(f"{key} = {_format_value(val)}")
        lines.append("")

    return "\n".join(lines).rstrip("\n") + "\n"


# ---------------------------------------------------------------------------
# dump_profile
# ---------------------------------------------------------------------------

def dump_profile(profile: dict) -> str:
    """Serialize an AI profile dict to deterministic TOML.

    Input shape (mirrors ai-profile.toml parsed by tomllib)::

        {
            "prompt_header": str,
            "extract": {"summary_field": str},
            "bill": {"app_token": str, "table_id": str},
            "fields": [
                {
                    "ai_key": str,
                    "feishu_field": str,
                    "type": str,
                    "target": str,
                    "fallback": str | None,   # omitted if None
                    "source": str | None,      # omitted if None
                    "prompt": str,
                },
            ],
        }
    """
    lines: list[str] = []

    # prompt_header MUST be first — bare key before any [section]
    lines.append(f"prompt_header = {_escape_str(profile['prompt_header'])}")
    lines.append("")

    extract = profile["extract"]
    lines.append("[extract]")
    lines.append(f"summary_field = {_escape_str(extract['summary_field'])}")
    lines.append("")

    bill = profile["bill"]
    lines.append("[bill]")
    lines.append(f"app_token = {_escape_str(bill['app_token'])}")
    lines.append(f"table_id = {_escape_str(bill['table_id'])}")
    lines.append("")

    for field in profile["fields"]:
        lines.append("[[fields]]")
        for key in _FIELD_KEY_ORDER:
            val = field.get(key)
            if val is None:
                continue
            lines.append(f"{key} = {_format_value(val)}")
        lines.append("")

    return "\n".join(lines).rstrip("\n") + "\n"