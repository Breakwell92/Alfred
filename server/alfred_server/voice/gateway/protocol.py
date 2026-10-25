"""Device WebSocket protocol, version 1: message types, validation and audio framing.

Text frames carry JSON control messages, each with a `type`; binary frames carry audio. The
full description, with examples, is docs/protocol.md. Keep the two in step.
"""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from alfred_server.agents.context import ID_PATTERN
from alfred_server.voice.stt import SAMPLE_RATE

PROTOCOL_VERSION = 1

INPUT_SAMPLE_RATE = SAMPLE_RATE
"""Audio from the device: mono signed 16-bit little-endian PCM at 16 kHz. Reply audio is
`voice.tts.OUTPUT_SAMPLE_RATE` (24 kHz), same encoding."""

AUDIO_FRAME = 0x01
"""First byte of a server → device binary frame holding reply audio."""

# Turn ids tag audio frames (one length byte), so they're short and ASCII.
TURN_ID_PATTERN = r"^[A-Za-z0-9._:-]{1,64}$"
# Free text that goes into the agent's system prompt: one line, no markup.
_CONTEXT_TEXT_PATTERN = r"^[^\r\n<>]{1,100}$"

# Names kept for later tickets so nothing else takes them: device tools (STU-81) and pushes
# (STU-84) in either direction.
RESERVED_TYPES = frozenset({"tool.call", "tool.result", "push.notification"})


class ProtocolError(ValueError):
    """A frame the server can't accept. `code` goes into the `error` message."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class _Message(BaseModel):
    # Unknown fields are ignored, so a newer app can talk to an older server.
    model_config = ConfigDict(extra="ignore")


class Hello(_Message):
    """First message on a connection: who the device is, and the token if not in a header."""

    type: Literal["hello"]
    protocol: int = PROTOCOL_VERSION
    device_id: str = Field(max_length=128, pattern=ID_PATTERN)
    # Only phones for now; room satellites (STU-102) will add a type.
    device_type: Literal["phone"] = "phone"
    app_version: str | None = Field(default=None, max_length=64)
    capabilities: list[Annotated[str, Field(max_length=64)]] = Field(
        default_factory=list, max_length=32
    )
    token: str | None = Field(default=None, max_length=512)
    # Continue this conversation instead of the device's current one.
    session_id: str | None = Field(default=None, max_length=128, pattern=ID_PATTERN)


class Ping(_Message):
    type: Literal["ping"]
    # Echoed back in the `pong`, e.g. to measure round trips.
    t: float | None = None


class TurnContext(_Message):
    active_room: str | None = Field(default=None, pattern=_CONTEXT_TEXT_PATTERN)
    location: str | None = Field(default=None, pattern=_CONTEXT_TEXT_PATTERN)


class TurnStart(_Message):
    type: Literal["turn.start"]
    turn_id: str = Field(pattern=TURN_ID_PATTERN)
    sample_rate: int = INPUT_SAMPLE_RATE
    persona_id: str | None = Field(default=None, max_length=64, pattern=ID_PATTERN)
    context: TurnContext = Field(default_factory=TurnContext)


class TurnEnd(_Message):
    type: Literal["turn.end"]
    turn_id: str = Field(pattern=TURN_ID_PATTERN)


class TurnCancel(_Message):
    type: Literal["turn.cancel"]
    turn_id: str = Field(pattern=TURN_ID_PATTERN)


class ConfirmReply(_Message):
    """A tap on the app's yes/no buttons for a `confirm.request`."""

    type: Literal["confirm.reply"]
    # At most 56 characters, so the default turn id `confirm-<id>` fits `TURN_ID_PATTERN`.
    confirmation_id: str = Field(max_length=56, pattern=r"^[A-Za-z0-9-]+$")
    approve: bool
    # Tags the spoken answer's audio; defaults to `confirm-<confirmation_id>`.
    turn_id: str | None = Field(default=None, pattern=TURN_ID_PATTERN)


ClientMessage = Annotated[
    Hello | Ping | TurnStart | TurnEnd | TurnCancel | ConfirmReply, Field(discriminator="type")
]
_CLIENT_MESSAGE: TypeAdapter[ClientMessage] = TypeAdapter(ClientMessage)


def parse_message(text: str) -> ClientMessage:
    """Validate one JSON control frame. Raises `ProtocolError`."""
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ProtocolError("bad_message", "Not valid JSON") from exc
    if not isinstance(data, dict) or not isinstance(data.get("type"), str):
        raise ProtocolError("bad_message", "Expected a JSON object with a 'type'")
    if data["type"] in RESERVED_TYPES:
        raise ProtocolError("unsupported", f"'{data['type']}' isn't supported yet")
    try:
        return _CLIENT_MESSAGE.validate_python(data)
    except ValidationError as exc:
        # Never echo the input: a hello can carry the API token.
        problems = "; ".join(
            f"{'.'.join(str(p) for p in e['loc']) or 'message'}: {e['msg']}"
            for e in exc.errors(include_input=False, include_url=False)
        )
        raise ProtocolError("bad_message", problems) from exc


def message(type_: str, **fields: Any) -> dict[str, Any]:
    """A server → device control message."""
    return {"type": type_, **fields}


def audio_frame(turn_id: str, pcm: bytes) -> bytes:
    """A reply-audio binary frame: `0x01`, turn id length, turn id (ASCII), PCM."""
    tag = turn_id.encode("ascii")
    return bytes([AUDIO_FRAME, len(tag)]) + tag + pcm


def parse_audio_frame(frame: bytes) -> tuple[str, bytes]:
    """`(turn_id, pcm)` from a reply-audio frame (for clients and tests)."""
    if len(frame) < 2 or frame[0] != AUDIO_FRAME or len(frame) < 2 + frame[1]:
        raise ProtocolError("bad_frame", "Not a reply-audio frame")
    end = 2 + frame[1]
    return frame[2:end].decode("ascii"), frame[end:]
