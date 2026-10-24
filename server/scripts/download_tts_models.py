"""Download the TTS model files into ALFRED_TTS_MODEL_DIR (default ~/.cache/alfred/tts).

Usage (from `server/`):
    uv run python scripts/download_tts_models.py                     # Kokoro + default Piper voice
    uv run python scripts/download_tts_models.py --piper en_GB-cori-high en_US-lessac-medium

Kokoro: the ONNX model and the voice pack from the kokoro-onnx releases (one file holds every
Kokoro voice). Piper: one .onnx + .onnx.json pair per voice, from the rhasspy/piper-voices
repository on Hugging Face. Files already present are skipped. See docs/decisions/0005-tts.md.
"""

from __future__ import annotations

import argparse
import shutil
import ssl
import sys
import urllib.request
from pathlib import Path

from alfred_server.config import get_settings
from alfred_server.voice.tts import resolve_model_dir

KOKORO_RELEASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"


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
    partial.replace(target)


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
        base = (
            "https://huggingface.co/rhasspy/piper-voices/resolve/main/"
            f"{lang.split('_')[0]}/{lang}/{speaker}/{quality}/{name}"
        )
        _download(f"{base}.onnx", model_dir / f"{name}.onnx")
        _download(f"{base}.onnx.json", model_dir / f"{name}.onnx.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
