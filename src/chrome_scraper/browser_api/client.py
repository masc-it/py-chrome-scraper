"""BrowserAPIClient: BrowserTool implementation backed by the browser-api HTTP service.

Scraping code (google_fetch, xcom_fetch) is unchanged — it calls browser.eval_js()
etc. which route over HTTP.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

import httpx

from chrome_scraper.web_scrapers.base import BrowserTab, WebScraperError

_DEFAULT_BASE_URL = "http://localhost:9333"


class BrowserAPIClient:
    """BrowserTool backed by a running browser-api server.

    Launch / stop are no-ops — the server owns the browser lifecycle.
    All page operations are HTTP calls keyed by tab id or label.
    """

    def __init__(self, base_url: str | None = None, *, timeout: float = 60.0):
        self.base_url = (
            base_url or os.environ.get("BROWSER_API_URL") or _DEFAULT_BASE_URL
        ).rstrip("/")
        self._http = httpx.Client(base_url=self.base_url, timeout=timeout)

    def launch(self, **kwargs: Any) -> dict[str, Any]:
        """No-op — browser is managed by the server. Returns status."""
        return self.status()

    def attach(self, **kwargs: Any) -> dict[str, Any]:
        """No-op — always attached to the running server. Returns status."""
        return self.status()

    def stop(self, *, timeout: float = 5.0) -> dict[str, Any]:
        """No-op — server stays alive. Does not shut down the browser."""
        return {"running": True, "stopped": False}

    def status(self) -> dict[str, Any]:
        r = self._get("/status")
        return r

    def list_tabs(self) -> list[BrowserTab]:
        tabs = self._get("/tabs")
        return [
            {
                "target_id": t["tab_id"],
                "title": t.get("title", ""),
                "url": t.get("url", ""),
                "label": t.get("label"),
            }
            for t in tabs
        ]

    def open_tab(
        self, url: str = "about:blank", *, label: str | None = None
    ) -> BrowserTab:
        r = self._post("/tabs", json={"url": url, "label": label})
        return {
            "target_id": r["tab_id"],
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "label": r.get("label"),
        }

    def close_tab(self, tab_ref: str) -> dict[str, Any]:
        self._delete(f"/tabs/{tab_ref}")
        return {"closed": True, "tab": {"target_id": tab_ref}}

    def activate_tab(self, tab_ref: str) -> dict[str, Any]:
        # No server-side activation needed — tabs are independent.
        return {"activated": True, "tab": {"target_id": tab_ref}}

    def navigate(
        self,
        *,
        tab_ref: str,
        url: str,
        timeout: float,
        wait_until: str = "load",
    ) -> dict[str, Any]:
        r = self._post(
            f"/tabs/{tab_ref}/goto",
            json={"url": url, "wait_until": wait_until},
            timeout=timeout,
        )
        return {"navigated": True, "wait_until": wait_until, "url": r["url"]}

    def eval_js(self, *, tab_ref: str, expression: str, timeout: float) -> Any:
        r = self._post(
            f"/tabs/{tab_ref}/eval",
            json={"expression": expression},
            timeout=timeout,
        )
        return r["result"]

    def eval_js_file(self, *, tab_ref: str, script_path: str, timeout: float) -> Any:
        from pathlib import Path

        script = Path(script_path).expanduser().read_text(encoding="utf-8")
        return self.eval_js(tab_ref=tab_ref, expression=script, timeout=timeout)

    def keyboard_type(self, *, tab_ref: str, text: str, delay_ms: int = 30) -> None:
        self._post(f"/tabs/{tab_ref}/type", json={"text": text, "delay_ms": delay_ms})

    def keyboard_press(self, *, tab_ref: str, key: str) -> None:
        self._post(f"/tabs/{tab_ref}/press", json={"key": key})

    def focus(self, *, tab_ref: str, selector: str, timeout: float) -> None:
        self._post(
            f"/tabs/{tab_ref}/focus", json={"selector": selector}, timeout=timeout
        )

    @contextmanager
    def tab(
        self,
        label: str,
        *,
        url: str = "about:blank",
        stop_on_exit: bool = True,
    ) -> Generator[str, None, None]:
        """Open a tab, yield its label, close tab on exit.

        stop_on_exit is ignored — the server owns the browser lifecycle.
        """
        self.open_tab(url, label=label)
        try:
            yield label
        finally:
            try:
                self.close_tab(label)
            except Exception:
                pass

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        """Dispatch an HTTP request and handle common errors."""
        try:
            r = getattr(self._http, method)(path, **kwargs)
            r.raise_for_status()
            return r.json()
        except httpx.ConnectError:
            raise WebScraperError(
                f"Cannot connect to browser-api at {self.base_url}. "
                "Start it with: uv run browser-api"
            )
        except httpx.HTTPStatusError as exc:
            raise WebScraperError(f"browser-api error: {exc.response.text}")

    def _get(self, path: str) -> Any:
        return self._request("get", path)

    def _post(
        self, path: str, *, json: dict | None = None, timeout: float | None = None
    ) -> Any:
        kw: dict[str, Any] = {}
        if json is not None:
            kw["json"] = json
        if timeout is not None:
            kw["timeout"] = timeout
        return self._request("post", path, **kw)

    def _delete(self, path: str) -> Any:
        return self._request("delete", path)
