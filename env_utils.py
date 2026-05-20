"""Чтение и запись настроек в .env."""

from __future__ import annotations

from pathlib import Path

ENV_FILE = Path(__file__).resolve().parent / ".env"


def read_env_value(key: str) -> str | None:
    """Последнее непустое значение ключа в .env (как у load_dotenv), без учёта строк-комментариев."""
    if not ENV_FILE.exists():
        return None
    prefix = f"{key}="
    last: str | None = None
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith(prefix):
            value = stripped[len(prefix) :].strip()
            last = value if value else None
    return last


def count_env_assignments(key: str) -> int:
    """Сколько раз в .env задан ключ (без комментариев). >1 — подозрение на разные токены."""
    if not ENV_FILE.exists():
        return 0
    prefix = f"{key}="
    n = 0
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith(prefix):
            n += 1
    return n


def write_env_value(key: str, value: str | None) -> None:
    lines: list[str] = []
    if ENV_FILE.exists():
        lines = ENV_FILE.read_text(encoding="utf-8").splitlines()

    prefix = f"{key}="
    updated: list[str] = []
    found = False
    for line in lines:
        if line.startswith(prefix):
            found = True
            if value is not None:
                updated.append(f"{prefix}{value}")
        elif line.strip() or updated:
            updated.append(line)

    if not found and value is not None:
        if updated and updated[-1].strip():
            updated.append("")
        updated.append(f"{prefix}{value}")

    ENV_FILE.write_text("\n".join(updated).rstrip() + "\n", encoding="utf-8")
