"""Download the TTS model files into ALFRED_TTS_MODEL_DIR (default ~/.cache/alfred/tts).

Usage (from `server/`):
    uv run python scripts/download_tts_models.py                     # Kokoro + default Piper voice
    uv run python scripts/download_tts_models.py --piper en_GB-cori-high en_US-lessac-medium

Kokoro: the ONNX model and the voice pack from the kokoro-onnx releases (one file holds every
Kokoro voice). Piper: one .onnx + .onnx.json pair per voice, from the rhasspy/piper-voices
repository on Hugging Face, pinned to one commit. Files with a known SHA-256 (the Kokoro files
and the default Piper voice) are checked after download; a mismatch deletes the download and
fails. Files already present are skipped. See docs/decisions/0006-tts.md.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import ssl
import sys
import urllib.request
from pathlib import Path

from alfred_server.config import get_settings
from alfred_server.voice.tts import resolve_model_dir

KOKORO_RELEASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
# rhasspy/piper-voices commit; bump it (and add checksums) to pick up upstream changes.
PIPER_REVISION = "5041a7a49ae5b3a0f30e6837769eb03fdbb231bd"
PIPER_VOICES = f"https://huggingface.co/rhasspy/piper-voices/resolve/{PIPER_REVISION}"

SHA256 = {
    "kokoro-v1.0.onnx": "7d5df8ecf7d4b1878015a32686053fd0eebe2bc377234608764cc0ef3636a6c5",
    "kokoro-v1.0.fp16.onnx": "c1610a859f3bdea01107e73e50100685af38fff88f5cd8e5c56df109ec880204",
    "kokoro-v1.0.int8.onnx": "6e742170d309016e5891a994e1ce1559c702a2ccd0075e67ef7157974f6406cb",
    "voices-v1.0.bin": "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d",
    "en_GB-alan-medium.onnx": "0a309668932205e762801f1efc2736cd4b0120329622adf62be09e56339d3330",
    "en_GB-alan-medium.onnx.json": (
        "c0f0d124e5895c00e7c03b35dcc8287f319a6998a365b182deb5c8e752ee8c1e"
    ),
}


class ChecksumError(RuntimeError):
    pass


def _ssl_context() -> ssl.SSLContext:
    # Use the OS certificate store when available, so machines with HTTPS interception work.
    try:
        import truststore

        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except ImportError:
        return ssl.create_default_context()


def _download(url: str, target: Path) -> None:
    if target.exists():
        print(f"exists   {target.name}")
        return
    print(f"download {target.name} ...", flush=True)
    partial = target.with_suffix(target.suffix + ".part")
    with (
        urllib.request.urlopen(url, context=_ssl_context()) as response,
        partial.open("wb") as out,
    ):
        shutil.copyfileobj(response, out)
    expected = SHA256.get(target.name)
    if expected is not None:
        actual = _sha256(partial)
        if actual != expected:
            partial.unlink()
            raise ChecksumError(f"{target.name}: SHA-256 {actual}, expected {expected}")
    else:
        print(f"         (no known checksum for {target.name}; not verified)")
    partial.replace(target)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model-dir", type=Path, default=resolve_model_dir(settings))
    parser.add_argument(
        "--kokoro-model",
        default=settings.tts_kokoro_model,
        help="Kokoro ONNX file name, e.g. kokoro-v1.0.onnx, kokoro-v1.0.fp16.onnx",
    )
    parser.add_argument(
        "--piper", nargs="*", default=[settings.tts_piper_voice], help="Piper voice names"
    )
    parser.add_argument("--no-kokoro", action="store_true")
    args = parser.parse_args()

    model_dir: Path = args.model_dir
    model_dir.mkdir(parents=True, exist_ok=True)
    print(f"Model directory: {model_dir}")

    if not args.no_kokoro:
        _download(f"{KOKORO_RELEASE}/{args.kokoro_model}", model_dir / args.kokoro_model)
        voices = settings.tts_kokoro_voices
        _download(f"{KOKORO_RELEASE}/{voices}", model_dir / voices)

    for name in args.piper:
        # e.g. en_GB-alan-medium -> en/en_GB/alan/medium/en_GB-alan-medium.onnx
        lang, speaker, quality = name.split("-", 2)
        base = f"{PIPER_VOICES}/{lang.split('_')[0]}/{lang}/{speaker}/{quality}/{name}"
        _download(f"{base}.onnx", model_dir / f"{name}.onnx")
        _download(f"{base}.onnx.json", model_dir / f"{name}.onnx.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
