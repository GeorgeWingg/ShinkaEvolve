"""Fetch Codex subscription usage and plan details."""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import math
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import requests

try:  # Python 3.11+
    import tomllib  # type: ignore[attr-defined]
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore


DEFAULT_CHATGPT_BASE = "https://chatgpt.com/backend-api"
CHATGPT_HOST_PREFIXES = ("https://chatgpt.com", "https://chat.openai.com")
TIMEOUT_SECONDS = 20


class CodexUsageError(RuntimeError):
    """Raised when the Codex usage helper cannot proceed."""


@dataclasses.dataclass
class CodexAuthInfo:
    mode: str
    access_token: Optional[str]
    api_key: Optional[str]
    account_id: Optional[str]
    email: Optional[str]
    plan_type: Optional[str]


@dataclasses.dataclass
class RateLimitWindowData:
    label: str
    percent_used: float
    window_minutes: Optional[int]
    reset_at: Optional[int]
    reset_at_local: Optional[str]


@dataclasses.dataclass
class UsageSnapshot:
    plan: Optional[str]
    email: Optional[str]
    windows: List[RateLimitWindowData]
    raw: Dict[str, Any]

    def to_dict(self, include_raw: bool = False) -> Dict[str, Any]:
        data = {
            "plan": self.plan,
            "email": self.email,
            "windows": [
                {
                    "label": window.label,
                    "percent_used": window.percent_used,
                    "window_minutes": window.window_minutes,
                    "reset_at": window.reset_at,
                    "reset_at_local": window.reset_at_local,
                }
                for window in self.windows
            ],
        }
        if include_raw:
            data["raw"] = self.raw
        return data


def get_codex_home() -> Path:
    env = os.environ.get("CODEX_HOME")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".codex"


def load_base_url(codex_home: Path) -> str:
    config_path = codex_home / "config.toml"
    base_value: Optional[str] = None
    if config_path.exists():
        try:
            data = tomllib.loads(config_path.read_text())
            base_value = data.get("chatgpt_base_url") or data.get("base_url")
        except Exception as exc:  # pragma: no cover
            raise CodexUsageError(f"Failed to parse {config_path}: {exc}") from exc
    return normalize_base_url(base_value)


def normalize_base_url(candidate: Optional[str]) -> str:
    if not candidate:
        return DEFAULT_CHATGPT_BASE
    value = candidate.strip().rstrip("/")
    if not value:
        return DEFAULT_CHATGPT_BASE
    if any(value.startswith(prefix) for prefix in CHATGPT_HOST_PREFIXES) and "/backend-api" not in value:
        value = f"{value}/backend-api"
    return value


def load_auth_info(codex_home: Path) -> CodexAuthInfo:
    auth_path = codex_home / "auth.json"
    if not auth_path.exists():
        raise CodexUsageError(
            f"Codex auth file not found at {auth_path}. Run `codex login` first or set CODEX_HOME."
        )
    try:
        payload = json.loads(auth_path.read_text())
    except json.JSONDecodeError as exc:
        raise CodexUsageError(f"Failed to parse {auth_path}: {exc}") from exc

    tokens = payload.get("tokens") or {}
    access_token = tokens.get("access_token")
    account_id = tokens.get("account_id")
    id_token = tokens.get("id_token") or {}
    if not isinstance(id_token, dict):
        id_token = {}
    claims = tokens.get("id_token_claims")
    if isinstance(claims, dict):
        merged = claims.copy()
        merged.update(id_token)
        id_token = merged
    plan_type = id_token.get("plan_type") or id_token.get("planType")
    email = id_token.get("email")

    api_key = payload.get("openai_api_key")

    if access_token:
        return CodexAuthInfo(
            mode="chatgpt",
            access_token=access_token,
            api_key=None,
            account_id=account_id,
            email=email,
            plan_type=plan_type,
        )

    if api_key:
        return CodexAuthInfo(
            mode="api_key",
            access_token=None,
            api_key=api_key,
            account_id=None,
            email=None,
            plan_type=None,
        )

    raise CodexUsageError(
        f"No usable tokens or API key found in {auth_path}. Run `codex login` to authenticate."
    )


def build_usage_url(base_url: str) -> str:
    path = "wham/usage" if "/backend-api" in base_url else "api/codex/usage"
    return f"{base_url}/{path}"


def get_limits_duration(window_minutes: int) -> str:
    minutes = max(0, window_minutes)
    minutes_per_hour = 60
    minutes_per_day = 24 * minutes_per_hour
    minutes_per_week = 7 * minutes_per_day
    minutes_per_month = 30 * minutes_per_day
    rounding_bias = 3

    if minutes <= minutes_per_day + rounding_bias:
        adjusted = minutes + rounding_bias
        hours = max(1, adjusted // minutes_per_hour)
        return f"{hours}h"
    if minutes <= minutes_per_week + rounding_bias:
        return "weekly"
    if minutes <= minutes_per_month + rounding_bias:
        return "monthly"
    return "annual"


def format_reset_timestamp(reset_at: Optional[int]) -> Optional[str]:
    if not reset_at:
        return None
    try:
        dt_utc = dt.datetime.fromtimestamp(reset_at, tz=dt.timezone.utc)
    except (OSError, ValueError):  # pragma: no cover
        return None
    dt_local = dt_utc.astimezone()
    today = dt.datetime.now(dt_local.tzinfo).date()
    if dt_local.date() == today:
        return dt_local.strftime("%H:%M")
    return dt_local.strftime("%a %H:%M")


def build_headers(auth: CodexAuthInfo) -> Dict[str, str]:
    headers = {"User-Agent": "codex-usage-helper"}
    if auth.mode == "chatgpt":
        headers["Authorization"] = f"Bearer {auth.access_token}"
        if auth.account_id:
            headers["ChatGPT-Account-Id"] = auth.account_id
    elif auth.mode == "api_key":
        headers["Authorization"] = f"Bearer {auth.api_key}"
    else:  # pragma: no cover
        raise CodexUsageError(f"Unsupported auth mode {auth.mode}")
    return headers


def parse_windows(payload: Dict[str, Any]) -> List[RateLimitWindowData]:
    rate_limit = payload.get("rate_limit") or {}
    windows: List[RateLimitWindowData] = []
    for snapshot, fallback_label in [
        (rate_limit.get("primary_window"), "5h"),
        (rate_limit.get("secondary_window"), "weekly"),
    ]:
        if snapshot is None:
            continue
        used_percent = float(snapshot.get("used_percent", 0.0))
        seconds = snapshot.get("limit_window_seconds")
        window_minutes = None
        if isinstance(seconds, (int, float)) and seconds > 0:
            window_minutes = math.ceil(seconds / 60)
        label_source = get_limits_duration(window_minutes) if window_minutes else fallback_label
        reset_at = snapshot.get("reset_at")
        reset_local = format_reset_timestamp(reset_at if isinstance(reset_at, int) else None)
        windows.append(
            RateLimitWindowData(
                label=f"{label_source} limit",
                percent_used=used_percent,
                window_minutes=window_minutes,
                reset_at=reset_at if isinstance(reset_at, int) else None,
                reset_at_local=reset_local,
            )
        )
    return windows


def collect_usage(codex_home: Optional[Path] = None) -> UsageSnapshot:
    home = codex_home or get_codex_home()
    base_url = load_base_url(home)
    auth = load_auth_info(home)
    url = build_usage_url(base_url)
    headers = build_headers(auth)
    response = requests.get(url, headers=headers, timeout=TIMEOUT_SECONDS)
    if response.status_code >= 400:
        raise CodexUsageError(
            f"Usage request failed with status {response.status_code}: {response.text.strip()}"
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise CodexUsageError(f"Failed to decode usage payload: {exc}") from exc
    plan_type = payload.get("plan_type") or auth.plan_type
    plan_display = title_case(plan_type) if plan_type else None
    windows = parse_windows(payload)
    return UsageSnapshot(plan=plan_display, email=auth.email, windows=windows, raw=payload)


def title_case(value: str) -> str:
    if not value:
        return value
    return value[0].upper() + value[1:].lower()


def format_snapshot(snapshot: UsageSnapshot) -> str:
    plan = snapshot.plan or "<unknown plan>"
    lines = [f"Plan: {plan}"]
    if snapshot.email:
        lines.append(f"Email: {snapshot.email}")
    if not snapshot.windows:
        lines.append("Limits: data not available yet.")
    else:
        lines.append("Limits:")
        for window in snapshot.windows:
            reset = f", resets at {window.reset_at_local}" if window.reset_at_local else ""
            lines.append(f"  - {window.label}: {window.percent_used:.1f}% used{reset}")
    return "\n".join(lines)


def main(argv: Optional[Iterable[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Fetch Codex usage and subscription info.")
    parser.add_argument("--human", action="store_true", help="Print a friendly summary instead of JSON.")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")
    parser.add_argument(
        "--include-raw",
        action="store_true",
        help="Include the raw API payload in the JSON output.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        snapshot = collect_usage()
    except CodexUsageError as exc:
        parser.exit(1, f"{exc}\n")
    if args.human:
        print(format_snapshot(snapshot))
        return
    indent = 2 if args.pretty else None
    print(json.dumps(snapshot.to_dict(include_raw=args.include_raw), indent=indent))


if __name__ == "__main__":  # pragma: no cover
    main()
