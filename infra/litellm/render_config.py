"""Render infra/litellm/config.yaml for the proxy, then exec the proxy.

Runs inside the LiteLLM container (its Python has PyYAML):

    python render_config.py SRC DEST [-- litellm args...]

Two things LiteLLM's config can't do on its own:

- `${VAR}` / `${VAR:-default}` anywhere in the file are replaced from the environment, so the
  local model tags follow infra/.env.
- A `model_list` entry with `requires_env: [VAR, ...]` is dropped unless every VAR is set and
  non-empty. Cloud aliases use this, so an alias only exists when its API key does. The
  `requires_env` key itself is removed before LiteLLM sees the file.

Keys stay as `os.environ/VAR` references, so the rendered file holds no secrets.

It also extends TLS trust: PEM certificates in `$EXTRA_CA_DIR` (infra/certs/, for hosts whose
HTTPS is intercepted) are appended to certifi's bundle and LiteLLM is pointed at the result
with `SSL_CERT_FILE`. LiteLLM ignores the system trust store, so this is the only way in.
"""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def expand(text: str, env: Mapping[str, str]) -> str:
    """Replace `${VAR}` and `${VAR:-default}`; an unset VAR without a default is an error."""

    def sub(match: re.Match[str]) -> str:
        name, default = match.group(1), match.group(2)
        value = env.get(name, "")
        if value:
            return value
        if default is not None:
            return default
        raise SystemExit(f"render_config: ${{{name}}} is not set and has no default")

    return _VAR.sub(sub, text)


def render(text: str, env: Mapping[str, str]) -> tuple[dict[str, Any], list[str]]:
    """Return the config LiteLLM should load and the model names that were dropped."""
    config = yaml.safe_load(expand(text, env)) or {}
    kept, dropped = [], []
    for entry in config.get("model_list") or []:
        required = entry.pop("requires_env", None) or []
        if all(env.get(var) for var in required):
            kept.append(entry)
        else:
            dropped.append(entry["model_name"])
    config["model_list"] = kept
    return config, dropped


def build_ca_bundle(cert_dir: Path, base_bundle: Path, dest: Path) -> list[str]:
    """Write `base_bundle` plus every PEM certificate in `cert_dir` to `dest`.

    Returns the names of the files added. Writes nothing and returns [] when `cert_dir` is
    missing or holds no certificates, so a host without interception keeps the default bundle.
    """
    if not cert_dir.is_dir():
        return []
    extras = [
        path
        for path in sorted(cert_dir.iterdir())
        if path.suffix in {".crt", ".pem"}
        and "-----BEGIN CERTIFICATE-----" in path.read_text(encoding="utf-8", errors="replace")
    ]
    if not extras:
        return []
    parts = [base_bundle.read_text(encoding="utf-8")]
    parts += [path.read_text(encoding="utf-8") for path in extras]
    dest.write_text("\n".join(part.strip() + "\n" for part in parts), encoding="utf-8")
    return [path.name for path in extras]


def _trust_extra_cas() -> None:
    cert_dir = os.environ.get("EXTRA_CA_DIR")
    if not cert_dir:
        return
    import certifi  # LiteLLM's own dependency; imported here so tests don't need it

    dest = Path("/tmp/alfred-ca-bundle.pem")
    added = build_ca_bundle(Path(cert_dir), Path(certifi.where()), dest)
    if added:
        os.environ["SSL_CERT_FILE"] = str(dest)
        print(f"render_config: trusting extra CAs: {', '.join(added)}", flush=True)


def main(argv: list[str]) -> None:
    if len(argv) < 2:
        raise SystemExit("usage: render_config.py SRC DEST [-- command...]")
    src, dest, rest = argv[0], argv[1], argv[2:]
    with open(src, encoding="utf-8") as f:
        config, dropped = render(f.read(), os.environ)
    with open(dest, "w", encoding="utf-8") as f:
        yaml.safe_dump(config, f, sort_keys=False)
    names = ", ".join(m["model_name"] for m in config["model_list"]) or "none"
    print(f"render_config: aliases enabled: {names}", flush=True)
    if dropped:
        print(f"render_config: disabled (API key not set): {', '.join(dropped)}", flush=True)
    _trust_extra_cas()
    if rest and rest[0] == "--":
        rest = rest[1:]
    if rest:
        os.execvp(rest[0], rest)


if __name__ == "__main__":
    main(sys.argv[1:])
