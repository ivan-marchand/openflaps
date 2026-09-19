from __future__ import annotations

from urllib.parse import urlparse

from pydantic import AnyHttpUrl
from starlette.applications import Starlette

from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings

from .auth import SingleUserOAuthProvider, build_login_handler
from .config import Config
from .pico_client import PicoAPIError, PicoClient, PicoUnreachableError

_LOCAL_HOST_PATTERNS = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
_LOCAL_ORIGIN_PATTERNS = ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"]

# Must match char_set in firmware/attiny84/split_flap_display_async/split_flap_controller.h
# exactly (that's the hardware's fixed set of physical flaps). The Pico uppercases text
# before display, so lowercase letters are fine here and normalized for this check only.
DISPLAY_CHARSET = " ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.,:-!?/°"


def build_transport_security(config: Config) -> TransportSecuritySettings:
    """Allow the server's real public hostname through the Host-header check.

    The MCP SDK auto-enables DNS-rebinding protection when the ASGI app binds to
    127.0.0.1, but its auto-generated allowlist only covers localhost — any
    request arriving with a different Host header (e.g. forwarded through a
    reverse proxy or tunnel under the server's real hostname) is rejected with
    421 Misdirected Request. This makes the public hostname from `public_url`
    valid too, alongside the SDK's own localhost defaults for local testing.
    """
    public_host = urlparse(config.public_url).hostname or "localhost"
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[public_host, f"{public_host}:*", *_LOCAL_HOST_PATTERNS],
        allowed_origins=[f"https://{public_host}", f"http://{public_host}", *_LOCAL_ORIGIN_PATTERNS],
    )


def _translate_errors(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except PicoUnreachableError as exc:
        raise ToolError(str(exc)) from exc
    except PicoAPIError as exc:
        raise ToolError(f"Pico API error ({exc.status_code}): {exc}") from exc


def _check_text_supported(text: str) -> None:
    """Reject characters the display's physical flaps can't show instead of
    letting the ATtiny84 silently no-op that module (display_char() just
    returns early on an unrecognized character, leaving the module showing
    whatever it showed before — no error surfaces anywhere).
    """
    invalid = sorted({c for c in text.upper() if c not in DISPLAY_CHARSET})
    if invalid:
        raise ToolError(
            f"Text contains characters the display can't show: {''.join(invalid)!r}. "
            f"Supported characters (case-insensitive): {DISPLAY_CHARSET!r}"
        )


def _check_text_fits(pico: PicoClient, text: str) -> None:
    """Reject text too long for the display instead of letting the Pico
    silently truncate it (print_string() hard-truncates to module_count
    with no error). Reports the row breakdown too, since char_per_row
    hard-wraps with no word-breaking — knowing "2 rows of 8" rather than
    just "16 total" helps fit words to row boundaries.
    """
    state = _translate_errors(pico.get_state)
    module_count = state.get("module_count")
    if not isinstance(module_count, int) or len(text) <= module_count:
        return

    char_per_row = state.get("char_per_row")
    if isinstance(char_per_row, int) and char_per_row > 0:
        num_rows = -(-module_count // char_per_row)  # ceil division
        layout_desc = f"{num_rows} row(s) of up to {char_per_row} characters each, hard-wrapped with no word-breaking"
    else:
        layout_desc = "a single row"
    raise ToolError(
        f"Text is {len(text)} characters but the display only has {module_count} "
        f"characters available ({layout_desc}); shorten it rather than relying on truncation."
    )


def build_server(config: Config, pico_client: PicoClient | None = None) -> MCPServer:
    pico = pico_client or PicoClient(config.pico_base_url)
    provider = SingleUserOAuthProvider(config.token_store_path)

    mcp = MCPServer(
        "openflap-display",
        auth_server_provider=provider,
        auth=AuthSettings(
            issuer_url=AnyHttpUrl(config.public_url),
            resource_server_url=AnyHttpUrl(f"{config.public_url}/mcp"),
            client_registration_options=ClientRegistrationOptions(enabled=True),
            validate_token_resource=False,
        ),
    )

    @mcp.tool()
    def set_text(text: str) -> dict:
        """Display the given text on the split-flap display.

        Only these characters are physically available on the flaps
        (case-insensitive, other characters are rejected):
        ' ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.,:-!?/°'
        (must match DISPLAY_CHARSET in server.py — kept in sync manually
        since a plain literal here, unlike an f-string, actually becomes
        this tool's __doc__/description)

        Also rejected if longer than the display's total capacity
        (module_count) rather than silently truncated — check get_state()
        first, or shorten and retry if this errors. The error reports how
        many rows the display has and characters per row, since text
        hard-wraps at each row boundary with no word-breaking."""
        _check_text_supported(text)
        _check_text_fits(pico, text)
        return _translate_errors(pico.set_text, text)

    @mcp.tool()
    def set_layout(layout_name: str | None = None) -> dict:
        """Switch the display to layout mode, showing a saved layout template
        (e.g. a clock or date+temperature format). Omit layout_name to
        re-render the currently active layout; pass a name from get_layouts()
        to switch to that one instead."""
        return _translate_errors(pico.set_layout, layout_name)

    @mcp.tool()
    def home() -> dict:
        """Re-home all display modules."""
        return _translate_errors(pico.home)

    @mcp.tool()
    def ask_claude(prompt: str) -> dict:
        """Ask the Pico's own Claude integration to generate and display a response to prompt."""
        return _translate_errors(pico.ask_claude, prompt)

    @mcp.tool()
    def get_state() -> dict:
        """Get the current display state: mode, last text shown, and module count."""
        return _translate_errors(pico.get_state)

    @mcp.tool()
    def get_modules() -> list:
        """List all detected I2C display modules and their parameters."""
        return _translate_errors(pico.get_modules)

    @mcp.tool()
    def get_max_concurrent_modules() -> dict:
        """Get the max number of modules allowed to move simultaneously."""
        return _translate_errors(pico.get_max_concurrent_modules)

    @mcp.tool()
    def get_layout() -> dict:
        """Get the display's physical row layout (characters per row) —
        not the content layout templates, see get_layouts for those."""
        return _translate_errors(pico.get_layout)

    @mcp.tool()
    def get_timezone() -> dict:
        """Get the display's configured IANA timezone."""
        return _translate_errors(pico.get_timezone)

    @mcp.tool()
    def save_api_key(key: str) -> dict:
        """Set the Claude API key the Pico uses for its own /api/claude integration."""
        return _translate_errors(pico.save_api_key, key)

    @mcp.tool()
    def save_timezone(zone: str) -> dict:
        """Set the display's IANA timezone (e.g. 'America/New_York')."""
        return _translate_errors(pico.save_timezone, zone)

    @mcp.tool()
    def set_max_concurrent_modules(max_concurrent: int) -> dict:
        """Set the max number of modules allowed to move simultaneously."""
        return _translate_errors(pico.set_max_concurrent_modules, max_concurrent)

    @mcp.tool()
    def save_layout(char_per_row: int) -> dict:
        """Set characters per row; -1 keeps every module on a single row.
        Physical row layout only — not the content layout templates,
        see save_layouts for those."""
        return _translate_errors(pico.save_layout, char_per_row)

    @mcp.tool()
    def get_layouts() -> dict:
        """List saved content layout templates (name, template string, and
        required display width) and which one is active. A template mixes
        literal text with placeholders like {hours:02}{minutes:02} — see
        save_layouts for the syntax."""
        return _translate_errors(pico.get_layouts)

    @mcp.tool()
    def save_layouts(layouts: list[dict], active_layout: str | None = None) -> dict:
        """Replace the whole catalog of content layout templates in one call.
        layouts is a list of {"name": str, "template": str} objects — at
        least one is required, names must be unique and non-empty.
        Template placeholder syntax: {field} (natural width), {field:N}
        (space-padded/truncated to width N), {field:0N} (zero-padded).
        Available fields: month, month_name (JAN..DEC), day, year (width 2 or 4 only), hours,
        hours12, minutes, ampm, weekday, temperature_f, temperature_c, humidity,
        wind_mph, wind_kmh, wind_deg, wind_dir (compass, e.g. NNE), condition
        (e.g. CLR, RAIN, SNOW, TSTM).
        Example: "{month:02}/{day:02}/{year:2} {temperature_f:3}°F".
        active_layout optionally selects which entry becomes active; if
        omitted, the current active layout is kept if still present,
        otherwise the first entry is used."""
        return _translate_errors(pico.save_layouts, layouts, active_layout)

    @mcp.tool()
    def identify_module(address: int) -> dict:
        """Flash a module's address character so it can be visually identified."""
        return _translate_errors(pico.identify_module, address)

    @mcp.tool()
    def power_down_module(address: int) -> dict:
        """De-energize a single module's stepper coils immediately."""
        return _translate_errors(pico.power_down_module, address)

    @mcp.tool()
    def update_module(address: int, new_address: int, speed: int, offset: int) -> dict:
        """Update a module's I2C address, speed, and offset calibration."""
        return _translate_errors(pico.update_module, address, new_address, speed, offset)

    mcp.custom_route("/login", methods=["GET", "POST"])(
        build_login_handler(provider, config.auth_password)
    )

    return mcp


def build_app(config: Config) -> Starlette:
    return build_server(config).streamable_http_app(transport_security=build_transport_security(config))
