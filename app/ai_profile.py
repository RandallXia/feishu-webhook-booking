"""AI extraction profile parsing + hot-reloading registry.

Parses a profile TOML describing:
- [extract]: summary_field (the extract-table field that receives the AI summary)
- [bill]: app_token + table_id for the bill bitable
- prompt_header: system prompt prefix passed to the AI extractor
- [[fields]]: ordered FieldSpec list (one target="extract", rest target="bill")

AiProfileRegistry wraps parse_profile with mtime-based hot reload and a
pre-loaded single_select option whitelist snapshot — mirrors
TargetRegistry's snapshot-swap pattern but async (snapshot assembly needs
await feishu.list_fields). Lock discipline: network IO OUTSIDE the lock,
snapshot reference swap INSIDE the lock.
"""

from __future__ import annotations

import logging
import threading
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .config import Settings
from .feishu_client import FeishuClient, FeishuClientError
from .field_codec import FieldSpec


logger = logging.getLogger("feishu_webhook_service.ai_profile")


class ProfileConfigError(RuntimeError):
    pass


class AiProfileRegistryUnavailableError(RuntimeError):
    """Raised by get_snapshot() when the registry is fail-closed.

    get_status() is the NON-raising diagnostic analog used by admin/health
    endpoints in fail-closed state.
    """


@dataclass(frozen=True, slots=True)
class AiProfile:
    prompt_header: str
    summary_field: str
    bill_app_token: str
    bill_table_id: str
    fields: tuple[FieldSpec, ...]


_VALID_TYPES = {"text", "number", "single_select", "date", "passthrough"}
_VALID_TARGETS = {"extract", "bill"}


def parse_profile_text(text: str) -> AiProfile:
    """Parse a profile TOML string into an AiProfile.

    Pure text-level parser — no filesystem access. All ProfileConfigError
    messages raised here are part of the public contract (golden-locked by
    test_ai_profile_parse.py); the path wrapper parse_profile delegates here.
    """
    data = tomllib.loads(text)

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


def parse_profile(path: Path) -> AiProfile:
    """Path wrapper around parse_profile_text.

    Keeps the file-level error (Missing profile file: {path}) here;
    delegates all text-level parsing (and its ProfileConfigError messages)
    to parse_profile_text.
    """
    if not path.is_file():
        raise ProfileConfigError(f"Missing profile file: {path}")
    return parse_profile_text(path.read_text(encoding="utf-8"))


async def validate_profile_candidate(
    profile_text: str,
    feishu: FeishuClient,
    extract_app_token: str,
    extract_table_id: str,
) -> tuple[AiProfile | None, dict[str, set[str]], list[dict]]:
    """Validate a candidate profile against live Feishu table schemas.

    Used by the admin "test" route to dry-run a profile before installation.
    The extract-table三元组 (extract_app_token/extract_table_id) is passed
    explicitly — the caller resolves it via target_registry; this function
    does NOT read the registry (single-testable).

    Returns (profile, option_whitelists, errors):
      - profile: parsed AiProfile (None only when parse or bill list_fields
        fails — fail-closed: cannot validate fields without the bill schema)
      - option_whitelists: single_select feishu_field → option name set
        (empty when parse or bill list_fields failed)
      - errors: list of {path, message} dicts; collect-all semantics — never
        raises on a single defect, surfaces every defect at once

    Path taxonomy (stable contract for the UI):
      - "profile"                    parse_profile_text failed
      - "bill"                       bill list_fields failed (fail-closed)
      - "fields[i].fallback"         single_select fallback empty OR not in options
      - "fields[i].feishu_field"     target="bill" field name not in bill table
      - "extract"                    extract list_fields failed
      - "extract.summary_field"      summary_field not in extract table fields
    """
    errors: list[dict] = []

    try:
        profile = parse_profile_text(profile_text)
    except ProfileConfigError as exc:
        return None, {}, [{"path": "profile", "message": str(exc)}]

    # Fail-closed on bill schema: without it we can neither validate
    # single_select options nor bill field names, so refuse the candidate
    # outright (profile=None) rather than emit a partial verdict.
    try:
        bill_fields_map = await feishu.list_fields(
            profile.bill_app_token, profile.bill_table_id
        )
    except FeishuClientError as exc:
        return None, {}, [
            {"path": "bill", "message": f"list_fields failed: {exc}"}
        ]

    whitelists = _extract_whitelists(bill_fields_map)

    # Collect ALL defects — the UI shows them together so the user can fix the
    # whole profile at once (unlike the registry, which fail-fast raises on
    # the first single_select problem). Bill-side check expanded beyond
    # single_select: a misnamed text/number/date/passthrough field is also a
    # defect here, since this is the dry-run "does the profile fit the live
    # tables" verdict.
    bill_field_names = set(bill_fields_map.keys())
    for i, spec in enumerate(profile.fields):
        if spec.target != "bill":
            continue
        if spec.feishu_field not in bill_field_names:
            errors.append({
                "path": f"fields[{i}].feishu_field",
                "message": (
                    f"bill field {spec.feishu_field!r} (ai_key={spec.ai_key!r}) "
                    f"not found in bill table fields list"
                ),
            })
            # Don't `continue` — a missing field still leaves its single_select
            # options undefined, which is itself a defect the UI should surface
            # (collect-all semantics: both the missing field name AND the
            # fallback-without-options errors are real, distinct problems).
        if spec.type == "single_select":
            options = whitelists.get(spec.feishu_field)
            if not spec.fallback:
                errors.append({
                    "path": f"fields[{i}].fallback",
                    "message": (
                        f"single_select field {spec.feishu_field!r} "
                        f"(ai_key={spec.ai_key!r}) has no fallback"
                    ),
                })
            elif options is None or spec.fallback not in options:
                errors.append({
                    "path": f"fields[{i}].fallback",
                    "message": (
                        f"single_select field {spec.feishu_field!r} "
                        f"fallback {spec.fallback!r} is not in the available "
                        f"options"
                    ),
                })

    # Extract list_fields is NOT fail-closed — a bill-side valid candidate
    # still surfaces the extract error so the UI can show it.
    try:
        extract_fields_map = await feishu.list_fields(
            extract_app_token, extract_table_id
        )
    except FeishuClientError as exc:
        errors.append({
            "path": "extract",
            "message": f"list_fields failed: {exc}",
        })
        extract_fields_map = {}

    # Gap fix: summary_field must exist in the extract table — without this the
    # AI summary writeback would silently target a nonexistent column.
    if (
        "extract" not in {e["path"] for e in errors}
        and profile.summary_field not in extract_fields_map
    ):
        errors.append({
            "path": "extract.summary_field",
            "message": (
                f"summary_field {profile.summary_field!r} not found in "
                f"extract table fields"
            ),
        })

    # Profile returns even on validation failure — the UI may display the
    # parsed structure for editing. Errors empty ⇒ approved.
    return profile, whitelists, errors


@dataclass(frozen=True, slots=True)
class AiProfileSnapshot:
    """Immutable registry snapshot — profile + pre-loaded option whitelists.

    option_whitelists maps single_select feishu_field → set of allowed option
    names. Built once from list_fields at snapshot assembly time; encode_fields
    reads it without further IO. Pollution-prevention guarantee: a single_select
    value not in this set triggers the spec's fallback rather than auto-creating
    a dirty Feishu option.
    """

    profile: AiProfile
    option_whitelists: dict[str, set[str]]
    loaded_at: float
    source_mtime: float | None
    generation: int


def build_field_prompts(
    profile: AiProfile,
    option_whitelists: dict[str, set[str]],
) -> dict[str, str]:
    """Build field_prompts dict, injecting single_select option constraints.

    Each single_select spec's prompt is augmented with the available options
    (e.g. "必须从以下选项中选择一个值返回：【选项1/选项2】"), so the AI
    model knows the exact set of allowed values before generating a response.
    Non-single_select and passthrough fields return their prompt verbatim.
    """
    prompts: dict[str, str] = {}
    for spec in profile.fields:
        if spec.type == "passthrough":
            continue
        prompt = spec.prompt
        if spec.type == "single_select":
            options = option_whitelists.get(spec.feishu_field, set())
            if options:
                joined = "/".join(sorted(options))
                prompt = f"{prompt}。必须从以下选项中选择一个值返回：【{joined}】"
        prompts[spec.ai_key] = prompt
    return prompts


def _extract_whitelists(
    fields_map: dict[str, dict],
) -> dict[str, set[str]]:
    """Pull single_select option names out of a list_fields response.

    Mirrors _OptionWhitelistCache._extract_whitelists (pipeline.py) so the
    registry's snapshot is shape-compatible with encode_fields.
    """
    whitelists: dict[str, set[str]] = {}
    for field_name, field_def in fields_map.items():
        if not isinstance(field_def, dict):
            continue
        prop = field_def.get("property")
        if not isinstance(prop, dict):
            continue
        options = prop.get("options")
        if not isinstance(options, list):
            continue
        names = {
            opt.get("name")
            for opt in options
            if isinstance(opt, dict) and isinstance(opt.get("name"), str)
        }
        if names:
            whitelists[field_name] = names
    return whitelists


class AiProfileRegistry:
    """Hot-reloading AI profile + single_select whitelist registry.

    Mirrors TargetRegistry's snapshot/generation/mtime pattern but async:
    snapshot assembly calls `await feishu.list_fields`, so ALL IO methods are
    `async def`. Lock scope = snapshot reference swap ONLY — network IO happens
    outside the lock. Config errors fail-closed: _config_valid=False → routes 503.
    """

    def __init__(self, settings: Settings, feishu: FeishuClient) -> None:
        self._settings = settings
        self._feishu = feishu
        self._lock = threading.Lock()
        self._snapshot: AiProfileSnapshot | None = None
        self._generation = 0
        self._last_reload_check_at = 0.0
        self._last_reload_error: str | None = None
        self._config_valid = True

    async def load_initial(self) -> None:
        """Startup load — fail-fast: raises on error (mirrors TargetRegistry.load_initial)."""
        snapshot = await self._load_snapshot()
        with self._lock:
            self._snapshot = snapshot
            self._config_valid = True
            self._last_reload_error = None

    async def maybe_reload(self) -> None:
        """Throttled mtime check. No-op when interval hasn't elapsed or mtime unchanged.

        Does NOT propagate reload errors — unlike load_initial (fail-fast at
        startup), per-request reloads leave the registry fail-closed and let
        get_snapshot() raise AiProfileRegistryUnavailableError. This keeps a
        hot-reload glitch from crashing a running server.
        """
        profile_file = self._settings.ai_profile_file
        if profile_file is None:
            return

        now = time.time()
        if now - self._last_reload_check_at < self._settings.ai_profile_reload_interval_seconds:
            return
        self._last_reload_check_at = now

        with self._lock:
            snapshot = self._snapshot
            config_valid = self._config_valid

        current_mtime = self._stat_profile_file(profile_file)
        # Reload if mtime changed OR the registry is currently fail-closed
        # (a config error may have been fixed on disk).
        if snapshot and snapshot.source_mtime == current_mtime and config_valid:
            return

        try:
            await self.reload(force=True)
        except (ProfileConfigError, FeishuClientError) as exc:
            # _load_snapshot already set _config_valid=False + last_reload_error.
            # Swallow: the route's get_snapshot() will raise the typed 503.
            logger.warning("ai profile registry hot reload failed; serving fail-closed error=%s", exc)

    async def reload(self, *, force: bool) -> dict[str, object]:
        """Force reload. Returns get_status() (mirrors TargetRegistry.reload)."""
        if not force:
            return self.get_status()

        snapshot = await self._load_snapshot()
        with self._lock:
            self._snapshot = snapshot
            self._config_valid = True
            self._last_reload_error = None

        logger.info(
            "ai profile registry reloaded fields=%s generation=%s",
            len(snapshot.profile.fields),
            snapshot.generation,
        )
        return self.get_status()

    def get_snapshot(self) -> AiProfileSnapshot:
        """Sync read-only. Raises AiProfileRegistryUnavailableError if fail-closed."""
        with self._lock:
            snapshot = self._snapshot
            config_valid = self._config_valid

        if snapshot is None or not config_valid:
            raise AiProfileRegistryUnavailableError(
                "ai profile registry is temporarily unavailable"
            )
        return snapshot

    def get_status(self) -> dict[str, object]:
        """NON-raising diagnostic accessor (analog of TargetRegistry.describe)."""
        with self._lock:
            snapshot = self._snapshot
            return {
                "generation": snapshot.generation if snapshot else 0,
                "config_valid": self._config_valid,
                "last_reload_error": self._last_reload_error,
                "field_count": len(snapshot.profile.fields) if snapshot else 0,
                "source_path": (
                    str(self._settings.ai_profile_file)
                    if self._settings.ai_profile_file
                    else None
                ),
            }

    async def _load_snapshot(self) -> AiProfileSnapshot:
        """Assemble a new snapshot. On error: set fail-closed flags + re-raise."""
        try:
            profile = parse_profile(self._settings.ai_profile_file)  # type: ignore[arg-type]

            # Only the bill table needs option whitelists — the extract table
            # write is always text (no single_select options to guard).
            fields_map = await self._feishu.list_fields(
                profile.bill_app_token, profile.bill_table_id
            )
            whitelists = _extract_whitelists(fields_map)
            self._validate_whitelists(profile, whitelists)

            self._generation += 1
            profile_file = self._settings.ai_profile_file
            source_mtime = profile_file.stat().st_mtime if profile_file else None
            return AiProfileSnapshot(
                profile=profile,
                option_whitelists=whitelists,
                loaded_at=time.time(),
                source_mtime=source_mtime,
                generation=self._generation,
            )
        except (ProfileConfigError, FeishuClientError) as exc:
            # Fail-closed: pollute-nothing guarantee. A bad whitelist or network
            # failure means we cannot guarantee single_select values land inside
            # real options, so the registry refuses to serve.
            with self._lock:
                self._config_valid = False
                self._last_reload_error = str(exc)
            logger.error("ai profile registry reload failed error=%s", exc)
            raise

    @staticmethod
    def _validate_whitelists(
        profile: AiProfile, whitelists: dict[str, set[str]]
    ) -> None:
        """Fail-closed validation for every single_select spec.

        A single_select spec is unsafe to serve if:
        - its feishu_field is NOT in list_fields response (field doesn't exist), OR
        - its options set is empty (no allowed values), OR
        - its fallback is NOT in the options set (fallback can't rescue pollution).
        """
        for spec in profile.fields:
            if spec.type != "single_select":
                continue
            options = whitelists.get(spec.feishu_field)
            if options is None:
                raise ProfileConfigError(
                    f"single_select field {spec.feishu_field!r} not found in "
                    f"bill table fields list"
                )
            if not options:
                raise ProfileConfigError(
                    f"single_select field {spec.feishu_field!r} has no options"
                )
            if spec.fallback is None or spec.fallback not in options:
                raise ProfileConfigError(
                    f"single_select field {spec.feishu_field!r} fallback "
                    f"{spec.fallback!r} is not in the available options"
                )

    @staticmethod
    def _stat_profile_file(profile_file: Path) -> float:
        if not profile_file.is_file():
            raise ProfileConfigError(f"Missing profile file: {profile_file}")
        return profile_file.stat().st_mtime
