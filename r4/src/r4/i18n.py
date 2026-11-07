"""Bilingual locale model — Ja⇄En parity for CLI surfaces.

Minimal-intrusive:
- locale resolved once from env/config (no per-command localization plumbing)
- t(en, ja) selects by current locale
- rollout = swap strings where desired, no app restructuring
"""

from __future__ import annotations

import locale as _syslocale
import os
from typing import Literal

Locale = Literal["en", "ja"]

_from_env = os.environ.get("R4_LOCALE") or os.environ.get("LANG") or ""

_default: Locale = "ja" if _from_env.startswith("ja_JP") else "en"

_locale: Locale = _default


def set_locale(next_locale: Locale) -> None:
    """Override the active locale."""
    global _locale, _syslocale
    _locale = next_locale
    # Sympathetic LANG/LC_ALL hint for subprocesses expecting utf normalization
    encoding = "ja_JP" if next_locale == "ja" else "C"
    try:
        _syslocale.setlocale(_syslocale.LC_ALL, f"{encoding}.UTF-8")
    except _syslocale.Error:
        pass


def locale() -> Locale:
    """Current locale."""
    return _locale


def t(en: str, ja: str) -> str:
    """Select a string by current locale."""
    return ja if _locale == "ja" else en


# Bilingual error-hint catalog — single source of truth for `_die` hints.
# Keyed by ``error_id``; each value is (en, ja). Callers pass only
# ``error_id``; the hint text lives here so translation parity is auditable.
HINT_CATALOG: dict[str, tuple[str, str]] = {
    "project.not_found": (
        "list all projects with `r4 project list`.",
        "`r4 project list` で全プロジェクトを確認できます。",
    ),
    "project.import.path_missing": (
        "check the path exists and is readable by your user.",
        "パスが存在し、実行ユーザーが読み取れることを確認してください。",
    ),
    "client.package_missing": (
        "install from the repository root with `uv pip install -e ./r4`.",
        "リポジトリ直下で `uv pip install -e ./r4` を実行してインストールしてください。",
    ),
    "knowledge.verify_failed": (
        "check your platform connection with `r4 doctor`, then retry.",
        "`r4 doctor` でプラットフォーム接続を確認してから再試行してください。",
    ),
    "project.loop_id_required": (
        "e.g. `r4 project loop status <loop_id>`; see all loops with `r4 project loop list`.",
        "例: `r4 project loop status <loop_id>`。"
        "全ループは `r4 project loop list` で確認できます。",
    ),
    "project.loop_watch_timeout": (
        "loop continues on the server; check later with `status`, or Ctrl+C to leave watching.",
        "ループはサーバー上で継続します。後で `status` で確認するか、"
        "Ctrl+C で監視を終了してください。",
    ),
    "auth.provider_unknown": (
        "valid presets are gakunin, orcid, github. Show them with `r4 auth login --help`.",
        "使用できるプリセットは gakunin / orcid / github です。"
        "`r4 auth login --help` で確認できます。",
    ),
    "auth.provider_missing_client_id": (
        "each provider requires a client_id registered for your deployment; "
        "see the hint for registration instructions.",
        "各プロバイダの client_id はデプロイごとの登録が必要です。"
        "登録手順は hint を参照してください。",
    ),
}


def hint_for(error_id: str) -> str | None:
    """Look up the localized hint for an ``error_id``.

    Returns None when the id is not in the catalog so callers can fall
    back to a locally supplied hint string.
    """
    entry = HINT_CATALOG.get(error_id)
    if entry is None:
        return None
    return t(*entry)


def hint_label() -> str:
    """Localized label printed before a hint line."""
    return t("hint", "ヒント")
