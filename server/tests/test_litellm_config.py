"""Tests for infra/litellm/render_config.py against the real infra/litellm/config.yaml."""

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from alfred_server.models.gateway import MODEL_ALIASES

LITELLM_DIR = Path(__file__).resolve().parents[2] / "infra" / "litellm"
CONFIG = (LITELLM_DIR / "config.yaml").read_text(encoding="utf-8")


def load_render_config() -> ModuleType:
    spec = importlib.util.spec_from_file_location("render_config", LITELLM_DIR / "render_config.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


render_config = load_render_config()


def models(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {m["model_name"]: m for m in config["model_list"]}


def test_without_keys_only_local_aliases() -> None:
    config, dropped = render_config.render(CONFIG, {})

    assert list(models(config)) == ["local-router", "local-main"]
    assert dropped == ["cloud-claude", "cloud-gpt", "cloud-gemini"]
    local_main = models(config)["local-main"]["litellm_params"]
    assert local_main["model"] == "ollama_chat/gpt-oss:20b"
    assert local_main["api_base"] == "http://ollama:11434"


def test_key_enables_its_alias_only() -> None:
    config, dropped = render_config.render(CONFIG, {"ANTHROPIC_API_KEY": "sk-ant-test"})

    claude = models(config)["cloud-claude"]
    assert "requires_env" not in claude
    # The key stays an env reference: the rendered file holds no secrets.
    assert claude["litellm_params"]["api_key"] == "os.environ/ANTHROPIC_API_KEY"
    assert dropped == ["cloud-gpt", "cloud-gemini"]


def test_config_aliases_match_server_aliases() -> None:
    env = {"ANTHROPIC_API_KEY": "a", "OPENAI_API_KEY": "o", "GEMINI_API_KEY": "g"}
    config, dropped = render_config.render(CONFIG, env)

    assert tuple(models(config)) == MODEL_ALIASES
    assert dropped == []


def test_model_overrides_from_env() -> None:
    env = {"LITELLM_LOCAL_MAIN_MODEL": "mistral-small3.2:24b", "LITELLM_LOCAL_ROUTER_MODEL": ""}
    config, _ = render_config.render(CONFIG, env)

    assert models(config)["local-main"]["litellm_params"]["model"] == (
        "ollama_chat/mistral-small3.2:24b"
    )
    # Empty counts as unset, so compose can pass `${VAR:-}` through.
    assert models(config)["local-router"]["litellm_params"]["model"] == (
        "ollama_chat/qwen3:4b-instruct-2507-q4_K_M"
    )


def test_spend_logging_needs_master_key_and_database() -> None:
    config, _ = render_config.render(CONFIG, {})

    assert config["general_settings"]["master_key"] == "os.environ/LITELLM_MASTER_KEY"
    assert config["general_settings"]["database_url"] == "os.environ/DATABASE_URL"
    assert config["general_settings"]["store_prompts_in_spend_logs"] is False


def test_unset_variable_without_default_is_an_error() -> None:
    with pytest.raises(SystemExit, match="MISSING"):
        render_config.expand("model: ${MISSING}", {})


PEM = "-----BEGIN CERTIFICATE-----\nMIIBfake\n-----END CERTIFICATE-----\n"
DER = bytes([0x30, 0x82, 0x03, 0x10, 0x30, 0x82, 0x01, 0xF8])  # start of a DER export


def make_base(tmp_path: Path) -> Path:
    base = tmp_path / "cacert.pem"
    base.write_text("# base bundle\n" + PEM.replace("fake", "base"), encoding="utf-8")
    return base


def test_ca_bundle_appends_certs_to_base(tmp_path: Path) -> None:
    certs = tmp_path / "certs"
    certs.mkdir()
    (certs / "local-ca.crt").write_text(PEM, encoding="ascii")
    (certs / "other.pem").write_text(PEM.replace("fake", "other"), encoding="ascii")
    (certs / "README.md").write_text("not a cert", encoding="ascii")
    dest = tmp_path / "bundle.pem"

    added, skipped = render_config.build_ca_bundle(certs, make_base(tmp_path), dest)

    assert added == ["local-ca.crt", "other.pem"]
    assert skipped == []
    bundle = dest.read_text(encoding="utf-8")
    assert bundle.index("MIIBbase") < bundle.index("MIIBfake") < bundle.index("MIIBother")
    assert bundle.count("-----BEGIN CERTIFICATE-----") == 3


def test_ca_bundle_reports_non_pem_cert_files(tmp_path: Path) -> None:
    certs = tmp_path / "certs"
    certs.mkdir()
    (certs / "der-export.crt").write_bytes(DER)
    (certs / "utf16.crt").write_text(PEM, encoding="utf-16")  # PowerShell 5.1 `>`
    (certs / "empty.pem").write_bytes(b"")
    (certs / "good.crt").write_text(PEM, encoding="ascii")
    (certs / "notes.txt").write_text("not a cert suffix", encoding="ascii")
    dest = tmp_path / "bundle.pem"

    added, skipped = render_config.build_ca_bundle(certs, make_base(tmp_path), dest)

    assert added == ["good.crt"]
    assert skipped == ["der-export.crt", "empty.pem", "utf16.crt"]
    assert dest.read_text(encoding="utf-8").count("-----BEGIN CERTIFICATE-----") == 2


@pytest.mark.parametrize("make_dir", [False, True])
def test_ca_bundle_untouched_without_certs(tmp_path: Path, make_dir: bool) -> None:
    certs = tmp_path / "certs"
    if make_dir:
        certs.mkdir()
        (certs / "README.md").write_text("only docs here", encoding="ascii")
    dest = tmp_path / "bundle.pem"

    assert render_config.build_ca_bundle(certs, tmp_path / "missing.pem", dest) == ([], [])
    assert not dest.exists()


def test_trust_extra_cas_sets_ssl_cert_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    certs = tmp_path / "certs"
    certs.mkdir()
    (certs / "local-ca.crt").write_text(PEM, encoding="ascii")
    (certs / "der.crt").write_bytes(DER)
    dest = tmp_path / "bundle.pem"
    env = {"EXTRA_CA_DIR": str(certs), "SSL_CERT_FILE": "/etc/ssl/certs/ca-certificates.crt"}

    render_config.trust_extra_cas(env, base_bundle=make_base(tmp_path), dest=dest)

    assert env["SSL_CERT_FILE"] == str(dest)
    assert dest.exists()
    out = capsys.readouterr().out
    assert "ignoring der.crt: not a PEM certificate" in out
    assert "trusting extra CAs: local-ca.crt" in out


@pytest.mark.parametrize("cert_dir", [None, "empty"])
def test_trust_extra_cas_leaves_env_alone_without_certs(
    tmp_path: Path, cert_dir: str | None
) -> None:
    env = {"SSL_CERT_FILE": "/etc/ssl/certs/ca-certificates.crt"}
    if cert_dir:
        (tmp_path / cert_dir).mkdir()
        env["EXTRA_CA_DIR"] = str(tmp_path / cert_dir)
    dest = tmp_path / "bundle.pem"

    render_config.trust_extra_cas(env, base_bundle=make_base(tmp_path), dest=dest)

    assert env["SSL_CERT_FILE"] == "/etc/ssl/certs/ca-certificates.crt"
    assert not dest.exists()
