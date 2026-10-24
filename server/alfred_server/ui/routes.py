"""Static files for the web chat page: `GET /ui/chat`, `/ui/chat.js`, `/ui/chat.css`.

Like `/health` and the OAuth callback, these don't need the API token: a browser can't send
a bearer header when it opens a page, so the page asks for the token and sends it with every
API call (`/chat/stream`, `/confirmations/...`), which stay protected. The files hold no data or
secrets. The headers lock the page down: scripts and styles only from these files, no framing,
no referrer, no caching. See docs/decisions/0011-text-chat-ui.md.
"""

from functools import cache
from importlib.resources import files

from fastapi import APIRouter
from fastapi.responses import Response

router = APIRouter(prefix="/ui")

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
        "img-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


@cache
def _static(name: str) -> bytes:
    return files("alfred_server.ui").joinpath("static", name).read_bytes()


def _file(name: str, media_type: str) -> Response:
    return Response(_static(name), media_type=media_type, headers=SECURITY_HEADERS)


@router.get("/chat")
def chat_page() -> Response:
    return _file("chat.html", "text/html; charset=utf-8")


@router.get("/chat.js")
def chat_script() -> Response:
    return _file("chat.js", "text/javascript; charset=utf-8")


@router.get("/chat.css")
def chat_styles() -> Response:
    return _file("chat.css", "text/css; charset=utf-8")
