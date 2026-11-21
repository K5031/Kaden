"""Keychain access and the settings file behind RuntimeAPI.update_settings."""

from __future__ import annotations

import asyncio
import os
import tomllib
from collections.abc import Collection
from pathlib import Path
from typing import Any

import keyring
import keyring.errors
import tomli_w

from kadence.api import KEYCHAIN_SERVICE, Budget, SettingsUpdate

SETTINGS_FILE = "settings.toml"
BUDGET_INTS = ("max_tokens", "max_tool_calls")  # limits counted in whole things
BUDGET_NUMBERS = ("max_cost_usd", "max_duration_s")


def bad_limit(name: str, value: object) -> str | None:
    """What is wrong with a limit of the budget, or None: the one test of it, for a
    value to be saved and for one read from a file."""
    integral = name in BUDGET_INTS
    kinds = (int,) if integral else (int, float)
    if isinstance(value, bool) or not isinstance(value, kinds) or value < 0:
        return f"{name} must be a non-negative {'integer' if integral else 'number'}"
    return None


def runtime_changes(update: SettingsUpdate) -> dict[str, Any]:
    """What `update` writes of the budget, the permissions and the system prompt, as the
    settings file keeps them. ValueError for what the next start would refuse to read."""
    changes: dict[str, Any] = {}
    if update.system_prompt is not None:
        if not update.system_prompt.strip():
            raise ValueError("the system prompt is empty")
        changes["system_prompt"] = update.system_prompt
    if update.budget is not None:
        changes["budget"] = _limits(update.budget)
    if update.permissions is not None:
        changes["permissions"] = update.permissions.value
    return changes


def _limits(budget: Budget) -> dict[str, Any]:
    limits = {name: getattr(budget, name) for name in (*BUDGET_INTS, *BUDGET_NUMBERS)
              if getattr(budget, name) is not None}
    for name, value in limits.items():
        if (wrong := bad_limit(name, value)) is not None:
            raise ValueError(f"the limit {wrong}")
    return limits


def read_key(name: str) -> str | None:
    """The key from the environment, else the keychain; None if neither has it. Blocks:
    call it off the event loop."""
    if value := os.environ.get(name):
        return value
    try:
        return keyring.get_password(KEYCHAIN_SERVICE, name)
    except keyring.errors.KeyringError:
        return None


def read_secret(name: str) -> str | None:
    """A keychain entry only (no environment variable has such a name). Blocks."""
    try:
        return keyring.get_password(KEYCHAIN_SERVICE, name)
    except keyring.errors.KeyringError:
        return None


async def has_key(name: str | None) -> bool:
    return name is not None and await asyncio.to_thread(read_key, name) is not None


async def get_stored_key(name: str) -> str | None:
    try:
        return await asyncio.to_thread(keyring.get_password, KEYCHAIN_SERVICE, name)
    except keyring.errors.KeyringError:
        return None


async def store_key(name: str, value: str | None) -> None:
    """Write (or, with None, remove) a keychain entry. ValueError without a keychain."""
    try:
        if value is None:
            await asyncio.to_thread(keyring.delete_password, KEYCHAIN_SERVICE, name)
        else:
            await asyncio.to_thread(keyring.set_password, KEYCHAIN_SERVICE, name, value)
    except keyring.errors.PasswordDeleteError:
        pass
    except keyring.errors.KeyringError as e:
        raise ValueError(f"no keychain available ({e}); set {name} in the environment") from e


def save_settings(data_dir: Path, changes: dict[str, Any], *, tables: Collection[str] = ()
                  ) -> None:
    """Merge `changes` into the settings file and replace it atomically. A change to one
    of `tables` merges into that table."""
    path = data_dir / SETTINGS_FILE
    try:
        saved = tomllib.loads(path.read_text())
    except FileNotFoundError:
        saved = {}
    for key, value in changes.items():
        saved[key] = {**saved.get(key, {}), **value} if key in tables else value
    tmp = path.with_suffix(".tmp")
    tmp.write_text(tomli_w.dumps(saved, multiline_strings=True))
    os.replace(tmp, path)
