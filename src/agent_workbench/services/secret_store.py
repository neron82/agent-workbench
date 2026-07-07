"""Small local secret-file helper for provider API keys.

Secrets are never stored in the SQLite DB. Instead we keep an env-style file
at project scope and read it dynamically on each agent request so newly added
provider keys work without restarting the Flask server.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Dict, Optional

_DEFAULT_SECRETS_FILE = Path(__file__).resolve().parents[3] / ".workbench.secrets.env"
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def get_secrets_file() -> Path:
    raw = os.environ.get("WORKBENCH_SECRETS_FILE", "").strip()
    return Path(raw).expanduser() if raw else _DEFAULT_SECRETS_FILE


def normalize_env_var_name(label: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", label.upper()).strip("_")
    slug = slug or "PROVIDER"
    return f"WORKBENCH_PROVIDER_{slug}_API_KEY"


def is_valid_env_var_name(name: str) -> bool:
    return bool(_ENV_NAME_RE.match(name))


def load_saved_secrets(path: Optional[Path] = None) -> Dict[str, str]:
    secrets_path = path or get_secrets_file()
    if not secrets_path.exists():
        return {}

    data: Dict[str, str] = {}
    for raw_line in secrets_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not is_valid_env_var_name(key):
            continue
        if value.startswith('"') and value.endswith('"'):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                value = value[1:-1]
        data[key] = value
    return data


def save_secret(name: str, value: str, path: Optional[Path] = None) -> Path:
    if not is_valid_env_var_name(name):
        raise ValueError(f"Invalid env var name: {name!r}")
    secrets_path = path or get_secrets_file()
    secrets = load_saved_secrets(secrets_path)
    secrets[name] = value
    secrets_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Agent Workbench provider secrets — local only, loaded dynamically.",
        *[f"{key}={json.dumps(val)}" for key, val in sorted(secrets.items())],
        "",
    ]
    secrets_path.write_text("\n".join(lines), encoding="utf-8")
    return secrets_path


def delete_secret(name: str, path: Optional[Path] = None) -> bool:
    secrets_path = path or get_secrets_file()
    secrets = load_saved_secrets(secrets_path)
    if name not in secrets:
        return False
    del secrets[name]
    if not secrets:
        if secrets_path.exists():
            secrets_path.unlink()
        return True
    lines = [
        "# Agent Workbench provider secrets — local only, loaded dynamically.",
        *[f"{key}={json.dumps(val)}" for key, val in sorted(secrets.items())],
        "",
    ]
    secrets_path.write_text("\n".join(lines), encoding="utf-8")
    return True


def resolve_secret(name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    env_value = os.environ.get(name)
    if env_value:
        return env_value
    return load_saved_secrets().get(name)


def describe_secret_source(name: Optional[str]) -> str:
    if not name:
        return "not-required"
    if os.environ.get(name):
        return "process-env"
    if name in load_saved_secrets():
        return "saved-file"
    return "missing"
