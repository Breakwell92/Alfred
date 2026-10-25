"""Pairing the app and checking its signed approvals: service, `POST /devices/pair`, CLI."""

import base64
from datetime import timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from alfred_server.devices.__main__ import main
from alfred_server.devices.service import (
    ApprovalProof,
    ApprovalProofError,
    DeviceService,
    PairingError,
    approval_message,
    normalise_code,
)
from alfred_server.devices.store import DeviceStore, PairedDevice
from tests.budget_helpers import memory_engine
from tests.connector_helpers import Clock
from tests.device_helpers import FakeApp
from tests.test_chat import TOKEN, ChatHarness, RecordingDb


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def devices(clock: Clock) -> DeviceService:
    return DeviceService(DeviceStore(memory_engine()), clock=clock)


def spki(key: ec.EllipticCurvePrivateKey | rsa.RSAPrivateKey) -> str:
    der = key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return base64.b64encode(der).decode()


def test_the_signed_message_format_is_fixed() -> None:
    # The Android app builds the same bytes (ApprovalSignerTest asserts the same string).
    assert approval_message("dev-1", "conf-1", True, 1_700_000_000) == (
        b"alfred-approval-v1\ndev-1\nconf-1\ntrue\n1700000000"
    )
    assert approval_message("dev-1", "conf-1", False, 5).endswith(b"\nfalse\n5")


# --- Pairing -----------------------------------------------------------------------------------


def test_a_code_pairs_one_device(devices: DeviceService) -> None:
    code = devices.create_pairing_code("pixel")
    assert len(code) == 9 and code[4] == "-"

    device = devices.pair(code.lower(), FakeApp().public_key)  # typed in lower case

    assert device.name == "pixel"
    assert [d.id for d in devices.devices()] == [device.id]
    with pytest.raises(PairingError):
        devices.pair(code, FakeApp().public_key)


def test_codes_expire(devices: DeviceService, clock: Clock) -> None:
    code = devices.create_pairing_code("pixel", timedelta(minutes=10))
    clock.advance(minutes=10)
    with pytest.raises(PairingError):
        devices.pair(code, FakeApp().public_key)


def test_a_wrong_code_is_refused(devices: DeviceService) -> None:
    devices.create_pairing_code("pixel")
    with pytest.raises(PairingError):
        devices.pair("AAAA-AAAA", FakeApp().public_key)


def test_codes_are_stored_hashed(devices: DeviceService) -> None:
    code = devices.create_pairing_code("pixel")
    with devices.store.engine.connect() as conn:
        rows = conn.exec_driver_sql("SELECT code_hash FROM pairing_codes").all()
    assert normalise_code(code) not in rows[0][0]


def test_confusable_letters_are_read_as_digits() -> None:
    assert normalise_code("o1il-ab cd") == "0111ABCD"


@pytest.mark.parametrize(
    "key",
    [
        spki(rsa.generate_private_key(public_exponent=65537, key_size=2048)),
        spki(ec.generate_private_key(ec.SECP384R1())),
        base64.b64encode(b"not a key").decode(),
        "%%%",
    ],
    ids=["rsa", "p384", "garbage", "not-base64"],
)
def test_only_p256_keys_pair_and_a_bad_key_keeps_the_code(devices: DeviceService, key: str) -> None:
    code = devices.create_pairing_code("pixel")
    with pytest.raises(PairingError):
        devices.pair(code, key)
    devices.pair(code, FakeApp().public_key)


# --- Verifying approvals ---------------------------------------------------------------------


def proof(
    app: FakeApp, clock: Clock, confirmation_id: str = "c1", device_id: str | None = None
) -> ApprovalProof:
    return ApprovalProof.model_validate(app.proof(confirmation_id, at=clock(), device_id=device_id))


def test_a_signed_approval_verifies(devices: DeviceService, clock: Clock) -> None:
    app = FakeApp()
    app.pair(devices)

    device = devices.verify_approval(proof(app, clock), "c1", approve=True)

    assert device.id == app.device_id
    stored = devices.store.get(app.device_id)
    assert stored is not None and stored.last_used_at == clock()


@pytest.mark.parametrize(
    ("confirmation_id", "approve"), [("c2", True), ("c1", False)], ids=["other-id", "deny"]
)
def test_a_signature_over_a_different_answer_fails(
    devices: DeviceService, clock: Clock, confirmation_id: str, approve: bool
) -> None:
    app = FakeApp()
    app.pair(devices)
    with pytest.raises(ApprovalProofError):
        devices.verify_approval(proof(app, clock), confirmation_id, approve=approve)


def test_another_keys_signature_fails(devices: DeviceService, clock: Clock) -> None:
    app, impostor = FakeApp(), FakeApp()
    app.pair(devices)
    impostor.device_id = app.device_id  # claims the paired device, signs with its own key
    with pytest.raises(ApprovalProofError):
        devices.verify_approval(proof(impostor, clock), "c1", approve=True)


def test_another_devices_id_fails(devices: DeviceService, clock: Clock) -> None:
    app, other = FakeApp(), FakeApp()
    app.pair(devices)
    other.pair(devices, "tablet")
    with pytest.raises(ApprovalProofError):
        devices.verify_approval(proof(app, clock, device_id=other.device_id), "c1", True)


def test_unknown_devices_fail(devices: DeviceService, clock: Clock) -> None:
    app = FakeApp()
    app.device_id = "never-paired"
    with pytest.raises(ApprovalProofError):
        devices.verify_approval(proof(app, clock), "c1", approve=True)


def test_stale_or_future_approvals_fail(devices: DeviceService, clock: Clock) -> None:
    app = FakeApp()
    app.pair(devices)
    signed = proof(app, clock)
    clock.advance(minutes=5, seconds=1)
    with pytest.raises(ApprovalProofError):
        devices.verify_approval(signed, "c1", approve=True)
    clock.advance(minutes=-10, seconds=-2)
    with pytest.raises(ApprovalProofError):
        devices.verify_approval(signed, "c1", approve=True)


def test_revoked_devices_fail(devices: DeviceService, clock: Clock) -> None:
    app = FakeApp()
    app.pair(devices)
    assert devices.revoke(app.device_id)
    assert not devices.revoke(app.device_id)
    with pytest.raises(ApprovalProofError):
        devices.verify_approval(proof(app, clock), "c1", approve=True)


def test_a_malformed_signature_fails(devices: DeviceService, clock: Clock) -> None:
    app = FakeApp()
    app.pair(devices)
    bad = proof(app, clock).model_copy(update={"signature": "not base64!"})
    with pytest.raises(ApprovalProofError):
        devices.verify_approval(bad, "c1", approve=True)


# --- POST /devices/pair ------------------------------------------------------------------------


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ChatHarness:
    monkeypatch.setenv("ALFRED_API_TOKEN", TOKEN)
    return ChatHarness(RecordingDb(tmp_path / "agno.db"))


def test_pairing_through_the_api(harness: ChatHarness) -> None:
    code = harness.devices.create_pairing_code("pixel")
    app = FakeApp()

    response = harness.post("/devices/pair", code=code, public_key=app.public_key)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["name"] == "pixel"
    assert harness.devices.store.get(body["device_id"]) is not None
    again = harness.post("/devices/pair", code=code, public_key=app.public_key)
    assert again.status_code == 403


def test_pairing_rejects_a_bad_key_with_400(harness: ChatHarness) -> None:
    code = harness.devices.create_pairing_code("pixel")
    response = harness.post("/devices/pair", code=code, public_key="bm90IGEga2V5")
    assert response.status_code == 400


def test_pairing_needs_the_token(harness: ChatHarness) -> None:
    code = harness.devices.create_pairing_code("pixel")
    response = harness.client.post(
        "/devices/pair", json={"code": code, "public_key": FakeApp().public_key}
    )
    assert response.status_code == 401
    assert harness.devices.devices() == []


def test_pairing_storage_failure_is_503(
    harness: ChatHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*args: object) -> None:
        raise RuntimeError("postgres down")

    monkeypatch.setattr(harness.devices.store, "claim_code", broken)
    response = harness.post("/devices/pair", code="AAAA-AAAA", public_key=FakeApp().public_key)
    assert response.status_code == 503


# --- CLI ---------------------------------------------------------------------------------------


def test_cli_pair_list_revoke(devices: DeviceService, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["pair", "--name", "pixel"], devices) == 0
    code = capsys.readouterr().out.split(": ")[1].split()[0]
    device = devices.pair(code, FakeApp().public_key)

    assert main(["list"], devices) == 0
    listed = capsys.readouterr().out
    assert device.id in listed and "pixel" in listed and "active" in listed

    assert main(["revoke", device.id], devices) == 0
    assert main(["revoke", device.id], devices) == 1
    assert main(["list"], devices) == 0
    assert "revoked" in capsys.readouterr().out


def test_cli_reports_storage_failures(capsys: pytest.CaptureFixture[str]) -> None:
    class Broken(DeviceService):
        def devices(self) -> list[PairedDevice]:
            raise RuntimeError("postgres down")

    assert main(["list"], Broken(DeviceStore(memory_engine()))) == 1
    assert "postgres down" in capsys.readouterr().err
