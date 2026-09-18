from __future__ import annotations

from typing import Any

import httpx


class PicoUnreachableError(RuntimeError):
    """The Pico could not be reached over the network."""


class PicoAPIError(RuntimeError):
    """The Pico responded with a non-2xx status."""

    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


class PicoClient:
    def __init__(self, base_url: str, client: httpx.Client | None = None):
        self._base_url = base_url.rstrip("/")
        # Any call that touches mutation_lock (set_text, set_layout,
        # eight_ball, home, module calibration) can legitimately queue
        # behind another in-flight display dispatch -- the Pico's design
        # has a contended request simply wait for the lock rather than
        # erroring, and a single dispatch has been measured up to ~9s on
        # an 8-module display. 10s left no margin for that; 20s comfortably
        # covers one queued predecessor without masking a truly dead Pico
        # for long. ask_claude still overrides this with its own longer
        # timeout for its much longer profile.
        self._client = client or httpx.Client(base_url=self._base_url, timeout=20.0)

    def close(self) -> None:
        self._client.close()

    def _get(self, path: str) -> Any:
        return self._request("GET", path)

    def _post(self, path: str, json: dict[str, Any] | None = None) -> Any:
        return self._request("POST", path, json=json)

    def _request(
        self,
        method: str,
        path: str,
        json: dict[str, Any] | None = None,
        timeout: Any = httpx.USE_CLIENT_DEFAULT,
    ) -> Any:
        # The default must be httpx's USE_CLIENT_DEFAULT sentinel, NOT None:
        # httpx reads an explicit timeout=None as "no timeout at all", which
        # would silently strip the client's 10s default off every call site
        # that doesn't pass one.
        try:
            response = self._client.request(method, path, json=json, timeout=timeout)
        except httpx.RequestError as exc:
            raise PicoUnreachableError(f"Pico unreachable at {self._base_url}{path}: {exc}") from exc

        if response.status_code >= 400:
            try:
                body = response.json()
                message = body.get("error", response.text)
            except ValueError:
                message = response.text
            raise PicoAPIError(response.status_code, message)

        return response.json()

    # Display control
    def get_state(self) -> Any:
        return self._get("/api/state")

    def set_text(self, text: str) -> Any:
        return self._post("/api/text", {"text": text})

    def set_layout(self, layout_name: str | None = None) -> Any:
        body: dict[str, Any] = {"mode": "layout"}
        if layout_name is not None:
            body["layout"] = layout_name
        return self._post("/api/mode", body)

    def eight_ball(self) -> Any:
        return self._post("/api/8ball")

    def home(self) -> Any:
        return self._post("/api/home")

    def ask_claude(self, prompt: str) -> Any:
        # /api/claude no longer closes the connection early -- the whole
        # Claude round-trip now happens inside this one request (measured
        # ~14s live, and the Pico's own two 15s asyncio.wait_for phases put
        # the ceiling above 30s), so the default 10s client timeout would
        # spuriously raise PicoUnreachableError.
        return self._request("POST", "/api/claude", json={"prompt": prompt}, timeout=60.0)

    # Setup/config
    def save_api_key(self, key: str) -> Any:
        return self._post("/api/setup/apikey", {"key": key})

    def get_timezone(self) -> Any:
        return self._get("/api/timezone")

    def save_timezone(self, zone: str) -> Any:
        return self._post("/api/setup/timezone", {"zone": zone})

    def get_max_concurrent_modules(self) -> Any:
        return self._get("/api/max_concurrent_modules")

    def set_max_concurrent_modules(self, max_concurrent: int) -> Any:
        return self._post("/api/setup/max_concurrent_modules", {"max_concurrent": max_concurrent})

    def get_layout(self) -> Any:
        return self._get("/api/layout")

    def save_layout(self, char_per_row: int) -> Any:
        return self._post("/api/setup/layout", {"char_per_row": char_per_row})

    def get_layouts(self) -> Any:
        return self._get("/api/layouts")

    def save_layouts(self, layouts: list[dict[str, str]], active_layout: str | None = None) -> Any:
        return self._post("/api/layouts", {"layouts": layouts, "active_layout": active_layout})

    # Module calibration
    def get_modules(self) -> Any:
        return self._get("/api/modules")

    def update_module(self, address: int, new_address: int, speed: int, offset: int) -> Any:
        return self._post(
            "/api/modules",
            {"address": address, "new_address": new_address, "speed": speed, "offset": offset},
        )

    def identify_module(self, address: int) -> Any:
        return self._post("/api/modules/identify", {"address": address})

    def power_down_module(self, address: int) -> Any:
        return self._post("/api/modules/power_down", {"address": address})
