"""browser-api server: long-running FastAPI process that owns a Patchright browser.

Exposes tab-keyed HTTP endpoints for navigation, JS evaluation, and keyboard
input. Multiple clients (google-fetch, xcom-fetch, etc.) connect concurrently,
each working on its own tab. Chrome stays warm between calls.
"""

from __future__ import annotations

import re
import sys
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from chrome_scraper.html_to_md.extract import _EXTRACT_JS, _SCROLL_JS
from chrome_scraper.html_to_md.render import render_page
from chrome_scraper.web_scrapers.base import WAIT_UNTIL_MAP, probe_chrome_identity_async


class OpenTabRequest(BaseModel):
    url: str = "about:blank"
    label: str | None = None


class TabResponse(BaseModel):
    tab_id: str
    title: str
    url: str
    label: str | None = None


class GotoRequest(BaseModel):
    url: str
    wait_until: str = "load"


class EvalRequest(BaseModel):
    expression: str
    await_promise: bool = True


class TypeRequest(BaseModel):
    text: str
    delay_ms: int = 30


class PressRequest(BaseModel):
    key: str


class FocusRequest(BaseModel):
    selector: str


class ConvertRequest(BaseModel):
    url: str
    wait_until: str = "load"
    scroll: bool = True
    timeout: float | None = None


class ConvertResponse(BaseModel):
    url: str
    title: str
    markdown: str
    extract_count: int
    viewport: dict[str, Any] | None = None


class StatusResponse(BaseModel):
    running: bool
    channel: str
    headless: bool
    chrome_version: str
    user_agent: str
    tabs: list[TabResponse]


_CHROME_VERSION_RE = re.compile(r"Chrome/(\d+\.\d+\.\d+\.\d+)")



@dataclass
class TabEntry:
    page: Any  # patchright async Page
    label: str | None = None


_TABS: dict[str, TabEntry] = {}
_LABELS: dict[str, str] = {}


def _register_tab(page: Any, label: str | None = None) -> str:
    tab_id = uuid.uuid4().hex[:12]
    _TABS[tab_id] = TabEntry(page=page, label=label)
    if label is not None:
        _LABELS[label] = tab_id
    return tab_id


def _resolve_tab(tab_ref: str) -> tuple[str, Any]:
    """Resolve a tab reference (id or label) to (tab_id, page)."""
    if tab_ref in _TABS:
        return tab_ref, _TABS[tab_ref].page
    if tab_ref in _LABELS:
        tid = _LABELS[tab_ref]
        return tid, _TABS[tid].page
    available = sorted(_LABELS)
    suffix = f" Available labels: {', '.join(available)}." if available else ""
    raise HTTPException(status_code=404, detail=f"Unknown tab '{tab_ref}'.{suffix}")


def _unregister_tab(tab_id: str) -> None:
    entry = _TABS.pop(tab_id, None)
    if entry and entry.label:
        _LABELS.pop(entry.label, None)



@dataclass
class BrowserState:
    playwright: Any = None
    context: Any = None
    channel: str = "chrome"
    headless: bool = False
    user_agent: str = ""
    chrome_version: str = ""
    timeout_ms: int = 30000


_STATE = BrowserState()



@asynccontextmanager
async def lifespan(app: FastAPI):
    from patchright.async_api import async_playwright

    config: ServerConfig = app.state.config

    pw = await async_playwright().start()

    # Headless UA probe: patchright strips "HeadlessChrome" from the UA.
    forced_ua: str | None = None
    if config.headless:
        forced_ua = await probe_chrome_identity_async(
            pw, channel=config.channel, chrome_path=config.chrome_path
        )

    profile_dir = (
        Path(config.profile_dir).expanduser()
        if config.profile_dir
        else _default_profile()
    )
    profile_dir.mkdir(parents=True, exist_ok=True)

    launch_kwargs: dict[str, Any] = {
        "user_data_dir": str(profile_dir),
        "channel": config.channel,
        "headless": config.headless,
        "locale": config.locale,
        "timezone_id": config.timezone,
    }
    if config.headless:
        # Non-headless with no explicit viewport → OS window size.
        pass
    else:
        launch_kwargs["no_viewport"] = True
    if config.chrome_path:
        launch_kwargs["executable_path"] = config.chrome_path
    if config.browser_args:
        launch_kwargs["args"] = config.browser_args
    if config.proxy:
        launch_kwargs["proxy"] = {"server": config.proxy}
    if forced_ua:
        launch_kwargs["user_agent"] = forced_ua

    context = await pw.chromium.launch_persistent_context(**launch_kwargs)

    final_ua = forced_ua or await _read_context_ua(context)
    version_m = _CHROME_VERSION_RE.search(final_ua or "")
    chrome_version = version_m.group(1) if version_m else ""

    context.set_default_timeout(config.timeout * 1000)

    _STATE.playwright = pw
    _STATE.context = context
    _STATE.channel = config.channel
    _STATE.headless = config.headless
    _STATE.user_agent = final_ua
    _STATE.chrome_version = chrome_version
    _STATE.timeout_ms = int(config.timeout * 1000)

    # Register pre-existing pages (browser may open a default tab).
    for page in context.pages:
        _register_tab(page)
    context.on("page", lambda page: _register_tab(page))

    if config.hide and sys.platform == "darwin":
        _hide_chrome_app()

    print(
        f"[browser-api] listening on :{config.port}  "
        f"channel={config.channel} headless={config.headless}  "
        f"chrome={chrome_version}",
        file=sys.stderr,
        flush=True,
    )

    yield

    # Shutdown: close context (skip flush for speed).
    try:
        await pw.stop()
    except Exception:
        pass
    _TABS.clear()
    _LABELS.clear()



@dataclass
class ServerConfig:
    port: int = 9333
    channel: str = "chrome"
    headless: bool = False
    chrome_path: str | None = None
    profile_dir: str | None = None
    locale: str = "en-US"
    timezone: str = "America/New_York"
    proxy: str | None = None
    timeout: float = 30.0
    browser_args: list[str] = field(default_factory=list)
    hide: bool = False


def create_app(config: ServerConfig | None = None) -> FastAPI:
    if config is None:
        config = ServerConfig()

    app = FastAPI(
        title="browser-api",
        description="Shared Patchright browser for thebase scrapers.",
        lifespan=lifespan,
    )
    app.state.config = config

    @app.get("/status", response_model=StatusResponse)
    async def status():
        tabs = []
        for tid, entry in _TABS.items():
            tabs.append(
                TabResponse(
                    tab_id=tid,
                    title=await _safe_title(entry.page),
                    url=entry.page.url,
                    label=entry.label,
                )
            )
        return StatusResponse(
            running=True,
            channel=_STATE.channel,
            headless=_STATE.headless,
            chrome_version=_STATE.chrome_version,
            user_agent=_STATE.user_agent,
            tabs=tabs,
        )

    @app.post("/shutdown")
    async def shutdown():
        import asyncio

        async def _delayed_shutdown():
            await asyncio.sleep(0.3)
            # Force exit — lifespan cleanup handles browser teardown.
            import os

            os._exit(0)

        asyncio.create_task(_delayed_shutdown())
        return {"ok": True, "shutting_down": True}

    @app.post("/tabs", response_model=TabResponse)
    async def open_tab(req: OpenTabRequest):
        ctx = _require_context()
        if req.label and req.label in _LABELS:
            raise HTTPException(
                status_code=409, detail=f"Label '{req.label}' already in use."
            )
        page = await ctx.new_page()
        tab_id = _register_tab(page, req.label)
        if req.url and req.url != "about:blank":
            try:
                await page.goto(req.url, timeout=_STATE.timeout_ms)
            except Exception as exc:
                await page.close()
                _unregister_tab(tab_id)
                raise HTTPException(status_code=502, detail=f"Navigation failed: {exc}")
        return TabResponse(
            tab_id=tab_id,
            title=await _safe_title(page),
            url=page.url,
            label=req.label,
        )

    @app.get("/tabs", response_model=list[TabResponse])
    async def list_tabs():
        result = []
        for tid, entry in _TABS.items():
            result.append(
                TabResponse(
                    tab_id=tid,
                    title=await _safe_title(entry.page),
                    url=entry.page.url,
                    label=entry.label,
                )
            )
        return result

    @app.delete("/tabs/{tab_ref}")
    async def close_tab(tab_ref: str):
        tid, page = _resolve_tab(tab_ref)
        try:
            await page.close()
        except Exception:
            pass
        _unregister_tab(tid)
        return {"closed": True, "tab_id": tid}

    @app.post("/tabs/{tab_ref}/goto")
    async def goto(tab_ref: str, req: GotoRequest):
        _, page = _resolve_tab(tab_ref)
        pw_wait = WAIT_UNTIL_MAP.get(req.wait_until)
        if pw_wait is None:
            raise HTTPException(
                status_code=400, detail=f"Unsupported wait_until: {req.wait_until}"
            )
        try:
            await page.goto(req.url, wait_until=pw_wait, timeout=_STATE.timeout_ms)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Navigation failed: {exc}")
        return {"url": page.url}

    @app.post("/tabs/{tab_ref}/eval")
    async def eval_js(tab_ref: str, req: EvalRequest):
        _, page = _resolve_tab(tab_ref)
        try:
            result = await page.evaluate(req.expression)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"eval failed: {exc}")
        return {"result": result}

    @app.post("/tabs/{tab_ref}/back")
    async def back(tab_ref: str):
        _, page = _resolve_tab(tab_ref)
        try:
            await page.go_back(timeout=_STATE.timeout_ms)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"history.back failed: {exc}")
        return {"url": page.url}

    @app.post("/tabs/{tab_ref}/focus")
    async def focus(tab_ref: str, req: FocusRequest):
        _, page = _resolve_tab(tab_ref)
        try:
            await page.focus(req.selector, timeout=_STATE.timeout_ms)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"focus failed: {exc}")
        return {"focused": True}

    @app.post("/tabs/{tab_ref}/type")
    async def type_text(tab_ref: str, req: TypeRequest):
        _, page = _resolve_tab(tab_ref)
        try:
            await page.keyboard.type(req.text, delay=req.delay_ms)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"type failed: {exc}")
        return {"typed": True}

    @app.post("/tabs/{tab_ref}/press")
    async def press_key(tab_ref: str, req: PressRequest):
        _, page = _resolve_tab(tab_ref)
        try:
            await page.keyboard.press(req.key)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"press failed: {exc}")
        return {"pressed": True}

    @app.post("/get-page-as-md")
    async def get_page_as_md(req: ConvertRequest):
        ctx = _require_context()
        page = await ctx.new_page()
        try:
            pw_wait = WAIT_UNTIL_MAP.get(req.wait_until)
            if pw_wait is None:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unsupported wait_until: {req.wait_until}",
                )
            timeout_ms = int(
                (req.timeout if req.timeout is not None else _STATE.timeout_ms / 1000)
                * 1000
            )
            await page.goto(req.url, wait_until=pw_wait, timeout=timeout_ms)

            if req.scroll:
                try:
                    await page.evaluate(_SCROLL_JS)
                except Exception:
                    pass

            payload = (await page.evaluate(_EXTRACT_JS)) or {}

            items = payload.get("items") or []
            viewport = payload.get("viewport") or {}
            page_width = float(viewport.get("scroll_w") or viewport.get("w") or 1280)

            md = render_page(items, page_width)
            title = (payload.get("title") or "").strip()
            if title:
                md = f"# {title}\n\n{md}"

            from fastapi.responses import PlainTextResponse

            return PlainTextResponse(
                content=md,
                media_type="text/plain",
                headers={
                    "X-Page-Title": title,
                    "X-Page-Url": payload.get("url") or req.url,
                    "X-Extract-Count": str(len(items)),
                },
            )
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Conversion failed: {exc}")
        finally:
            try:
                await page.close()
            except Exception:
                pass

    return app



def _require_context():
    if _STATE.context is None:
        raise HTTPException(status_code=503, detail="Browser not started.")
    return _STATE.context


async def _safe_title(page: Any) -> str:
    try:
        return await page.title() if hasattr(page, "title") else ""
    except Exception:
        return ""


async def _read_context_ua(context: Any) -> str:
    pages = context.pages
    page = pages[0] if pages else await context.new_page()
    try:
        return str(await page.evaluate("navigator.userAgent"))
    except Exception:
        return ""


def _hide_chrome_app() -> None:
    """Hide Chrome Canary / Chrome from sight on macOS without quitting."""
    import subprocess

    for app_name in ("Google Chrome Canary", "Google Chrome"):
        try:
            subprocess.run(
                [
                    "osascript",
                    "-e",
                    f'tell application "System Events" to set visible of process "{app_name}" to false',
                ],
                capture_output=True,
                timeout=3,
            )
            return
        except (FileNotFoundError, subprocess.TimeoutExpired):
            break


def _default_profile() -> Path:
    """Profile path so saved cookies/logins carry over across restarts."""
    import os

    if sys.platform == "darwin":
        return (
            Path.home()
            / "Library"
            / "Application Support"
            / "chrome_scraper"
            / "playwright"
            / "profile"
        )
    if sys.platform == "win32":
        return (
            Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
            / "chrome_scraper"
            / "playwright"
            / "profile"
        )
    if state_home := os.environ.get("XDG_STATE_HOME"):
        return Path(state_home) / "chrome_scraper" / "playwright" / "profile"
    return Path.home() / ".local" / "state" / "chrome_scraper" / "playwright" / "profile"
