import secrets
import time
from html import escape

from fastapi import APIRouter, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from mcp.server.auth.provider import AuthorizationCode, construct_redirect_uri

from src.db import async_session_maker
from src.public.oauth import CODE_TTL, provider
from src.repository import login_codes

router = APIRouter()

PAGE = """<!doctype html>
<html lang="uk">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Вхід — BEST Lviv</title>
<style>
  body {{ font-family: system-ui, sans-serif; max-width: 26rem; margin: 3rem auto;
         padding: 0 1rem; line-height: 1.5; }}
  input, button {{ font-size: 1.2rem; padding: .6rem; width: 100%;
                   box-sizing: border-box; margin-top: .5rem; }}
  input {{ letter-spacing: .2em; text-transform: uppercase; }}
  .error {{ color: #b00020; }}
</style>
</head>
<body>
<h1>Вхід до бази BEST Lviv</h1>
{body}
</body>
</html>"""

FORM = """<p>Надішли боту BEST Lviv у Telegram команду <b>/mcp_login</b>
і встав сюди код, який він дасть.</p>
{error}
<form method="post">
  <input type="hidden" name="pending" value="{pending}">
  <input name="code" autocomplete="one-time-code" autofocus required>
  <button type="submit">Увійти</button>
</form>"""

EXPIRED = """<p class="error">Посилання для входу застаріло або вже використане.
Почни підключення заново там, звідки прийшов.</p>"""


def _form(pending: str, error: str = "") -> HTMLResponse:
    error_html = f'<p class="error">{escape(error)}</p>' if error else ""
    body = FORM.format(pending=escape(pending), error=error_html)
    return HTMLResponse(PAGE.format(body=body))


def _alive(pending: str) -> bool:
    entry = provider.pending.get(pending)
    return entry is not None and entry.expires_at > time.time()


@router.get("/login")
async def login_form(pending: str):
    if not _alive(pending):
        return HTMLResponse(PAGE.format(body=EXPIRED), status_code=400)
    return _form(pending)


@router.post("/login")
async def login_submit(pending: str = Form(), code: str = Form()):
    if not _alive(pending):
        return HTMLResponse(PAGE.format(body=EXPIRED), status_code=400)

    async with async_session_maker() as db:
        user_id = await login_codes.take(db, code)
    if user_id is None:
        return _form(pending, "Код не підходить або вже прострочений. Попроси новий.")

    entry = provider.pending.pop(pending)
    auth_code = secrets.token_urlsafe(32)
    provider.codes[auth_code] = AuthorizationCode(
        code=auth_code,
        scopes=[],
        expires_at=time.time() + CODE_TTL,
        client_id=entry.client_id,
        code_challenge=entry.params.code_challenge,
        redirect_uri=entry.params.redirect_uri,
        redirect_uri_provided_explicitly=entry.params.redirect_uri_provided_explicitly,
        resource=entry.params.resource,
        subject=str(user_id),
    )
    redirect = construct_redirect_uri(
        str(entry.params.redirect_uri), code=auth_code, state=entry.params.state
    )
    return RedirectResponse(redirect, status_code=302)
