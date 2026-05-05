"""Cloudflare MCP Server — DNS records, cache purge, Workers routes."""

import argparse
import logging
import re
import sys
from typing import Any

import httpx
from fastmcp import FastMCP

from clr_cloudflare_mcp.config import Settings
from clr_cloudflare_mcp.middleware import ToolValidationMiddleware

mcp = FastMCP("Cloudflare Extra")
mcp.add_middleware(ToolValidationMiddleware())
_settings: Settings | None = None

# Imported here (not at the top) on purpose: annotations.py needs ``mcp`` from
# this module, so importing it before the ``mcp = FastMCP(...)`` line above
# would be a circular import. Do not move.
from clr_cloudflare_mcp.annotations import (  # noqa: E402
    destructive_tool,
    read_tool,
    remove_non_read_tools,
    write_tool,
)

API_BASE = "https://api.cloudflare.com/client/v4"

# Zone ID cache to avoid repeated lookups
_zone_cache: dict[str, str] = {}


# ── Helpers ─────────────────────────────────────────────────────────


def _headers() -> dict[str, str]:
    """Build HTTP headers with Cloudflare API bearer-token authorization.

    Returns:
        A dictionary of HTTP headers including Authorization and Content-Type.
    """
    creds = _settings.load_credentials()
    token = creds.get("api_token", "")
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def _request(method: str, endpoint: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    """Make a Cloudflare API request and return the JSON response.

    Args:
        method: HTTP method (GET, POST, PUT, DELETE, etc.).
        endpoint: API endpoint path relative to the Cloudflare v4 base URL.
        data: Optional JSON body payload.

    Returns:
        The parsed JSON response dictionary.

    Raises:
        Exception: If the Cloudflare API returns an error response.
    """
    url = f"{API_BASE}/{endpoint}"
    resp = httpx.request(method, url, headers=_headers(), json=data, timeout=30)
    result = resp.json()

    if not result.get("success", False):
        errors = result.get("errors", [])
        if errors:
            parts = []
            for e in errors:
                code = e.get("code", "")
                msg = e.get("message", str(e))
                if code == 10000:
                    parts.append("Authentication failed - check your API token")
                elif code == 7003:
                    parts.append("Zone not found or no permission")
                elif code == 81057:
                    parts.append("Record already exists")
                else:
                    parts.append(f"{msg} (code {code})" if code else msg)
            raise Exception("; ".join(parts))
        raise Exception(f"API error (status {resp.status_code})")

    return result


def _resolve_zone_id(domain: str) -> str:
    """Resolve a domain name to its Cloudflare zone ID.

    If *domain* is already a 32-character hex zone ID it is returned as-is.
    Otherwise, the zone list is queried and the result is cached.

    Args:
        domain: Domain name (e.g. ``example.com``) or 32-char hex zone ID.

    Returns:
        The Cloudflare zone ID string.

    Raises:
        Exception: If the zone cannot be found.
    """
    if domain in _zone_cache:
        return _zone_cache[domain]
    if re.match(r"^[0-9a-f]{32}$", domain):
        return domain
    result = _request("GET", "zones?per_page=50")
    for zone in result.get("result", []):
        if zone.get("name") == domain:
            _zone_cache[domain] = zone["id"]
            return zone["id"]
    raise Exception(f"Zone not found: {domain}")


# ── Zone Tools ──────────────────────────────────────────────────────


@read_tool
def cf_verify_token() -> dict[str, Any]:
    """Verify that the Cloudflare API token is valid.

    Returns:
        A dictionary with the token ``status`` and ``id``.
    """
    result = _request("GET", "user/tokens/verify")
    info = result.get("result", {})
    return {"status": info.get("status", "unknown"), "id": info.get("id", "N/A")}


@read_tool
def cf_list_zones() -> list[dict[str, Any]]:
    """List all Cloudflare zones (domains) in the account.

    Returns:
        A list of zone dictionaries containing id, name, status, and name_servers.
    """
    result = _request("GET", "zones?per_page=50")
    return [
        {
            "id": z["id"],
            "name": z["name"],
            "status": z.get("status"),
            "name_servers": z.get("name_servers", []),
        }
        for z in result.get("result", [])
    ]


@read_tool
def cf_get_zone(domain: str) -> dict[str, Any]:
    """Get details for a specific zone.

    Args:
        domain: Domain name or zone ID.

    Returns:
        A dictionary with zone id, name, status, name_servers, and plan.
    """
    zone_id = _resolve_zone_id(domain)
    result = _request("GET", f"zones/{zone_id}")
    z = result.get("result", {})
    return {
        "id": z.get("id"),
        "name": z.get("name"),
        "status": z.get("status"),
        "name_servers": z.get("name_servers", []),
        "plan": z.get("plan", {}).get("name"),
    }


# ── DNS Tools ───────────────────────────────────────────────────────


@read_tool
def cf_list_dns_records(domain: str) -> list[dict[str, Any]]:
    """List all DNS records for a domain.

    Args:
        domain: Domain name or zone ID.

    Returns:
        A list of DNS record dictionaries with id, type, name, content,
        proxied, and ttl fields.
    """
    zone_id = _resolve_zone_id(domain)
    result = _request("GET", f"zones/{zone_id}/dns_records")
    return [
        {
            "id": r["id"],
            "type": r["type"],
            "name": r["name"],
            "content": r["content"],
            "proxied": r.get("proxied", False),
            "ttl": r.get("ttl"),
        }
        for r in result.get("result", [])
    ]


@write_tool
def cf_add_dns_record(
    domain: str,
    record_type: str,
    name: str,
    content: str,
    proxied: bool = False,
    ttl: int = 1,
) -> dict[str, Any]:
    """Create a new DNS record.

    Args:
        domain: Domain name or zone ID.
        record_type: Record type (A, AAAA, CNAME, MX, TXT, SRV, CAA, NS).
        name: Record name (use @ for apex domain).
        content: Record content (IP, hostname, text value).
        proxied: Enable Cloudflare proxy (orange cloud). Only for A/AAAA/CNAME.
        ttl: TTL in seconds (1 = auto).

    Returns:
        A dictionary with the created record's id, type, name, and content.
    """
    zone_id = _resolve_zone_id(domain)
    data = {
        "type": record_type.upper(),
        "name": name,
        "content": content,
        "proxied": proxied,
        "ttl": ttl,
    }
    result = _request("POST", f"zones/{zone_id}/dns_records", data)
    r = result.get("result", {})
    return {"id": r.get("id"), "type": r.get("type"), "name": r.get("name"), "content": r.get("content")}


@write_tool
def cf_update_dns_record(
    domain: str,
    record_id: str,
    content: str | None = None,
    proxied: bool | None = None,
) -> dict[str, Any]:
    """Update an existing DNS record.

    Args:
        domain: Domain name or zone ID.
        record_id: The DNS record ID to update.
        content: New record content (IP, hostname, text value).
        proxied: Enable/disable Cloudflare proxy.

    Returns:
        A dictionary with the updated record's id, type, name, and content.
    """
    zone_id = _resolve_zone_id(domain)
    existing = _request("GET", f"zones/{zone_id}/dns_records/{record_id}")
    record = existing.get("result", {})
    data = {
        "type": record.get("type"),
        "name": record.get("name"),
        "content": content if content is not None else record.get("content"),
        "proxied": proxied if proxied is not None else record.get("proxied"),
        "ttl": record.get("ttl", 1),
    }
    result = _request("PUT", f"zones/{zone_id}/dns_records/{record_id}", data)
    r = result.get("result", {})
    return {"id": r.get("id"), "type": r.get("type"), "name": r.get("name"), "content": r.get("content")}


@destructive_tool
def cf_delete_dns_record(domain: str, record_id: str) -> dict[str, str]:
    """Delete a DNS record.

    Args:
        domain: Domain name or zone ID.
        record_id: The DNS record ID to delete.

    Returns:
        A dictionary with status ``"deleted"`` and the deleted record_id.
    """
    zone_id = _resolve_zone_id(domain)
    _request("DELETE", f"zones/{zone_id}/dns_records/{record_id}")
    return {"status": "deleted", "record_id": record_id}


# ── Cache Tools ─────────────────────────────────────────────────────


@destructive_tool
def cf_purge_cache(
    domain: str,
    purge_all: bool = False,
    urls: list[str] | None = None,
    prefixes: list[str] | None = None,
) -> dict[str, Any]:
    """Purge cached content for a domain.

    Args:
        domain: Domain name or zone ID.
        purge_all: Purge everything (use with caution).
        urls: List of specific URLs to purge.
        prefixes: List of URL prefixes to purge.

    Returns:
        A dictionary with status ``"purged"`` and the purge job id.

    Raises:
        Exception: If none of purge_all, urls, or prefixes is specified.
    """
    zone_id = _resolve_zone_id(domain)
    if purge_all:
        data = {"purge_everything": True}
    elif urls:
        data = {"files": urls}
    elif prefixes:
        data = {"prefixes": prefixes}
    else:
        raise Exception("Must specify purge_all=True, urls, or prefixes")
    result = _request("POST", f"zones/{zone_id}/purge_cache", data)
    return {"status": "purged", "id": result.get("result", {}).get("id")}


# ── Workers Routes Tools ────────────────────────────────────────────


@read_tool
def cf_list_routes(domain: str) -> list[dict[str, Any]]:
    """List Workers routes for a domain.

    Args:
        domain: Domain name or zone ID.

    Returns:
        A list of route dictionaries with id, pattern, and script fields.
    """
    zone_id = _resolve_zone_id(domain)
    result = _request("GET", f"zones/{zone_id}/workers/routes")
    return [
        {"id": r.get("id"), "pattern": r.get("pattern"), "script": r.get("script")}
        for r in result.get("result", [])
    ]


@write_tool
def cf_add_route(domain: str, pattern: str, worker: str) -> dict[str, Any]:
    """Create a Workers route for a domain.

    Args:
        domain: Domain name or zone ID.
        pattern: Route pattern (e.g. example.com/api/*).
        worker: Worker script name.

    Returns:
        A dictionary with the new route's id, pattern, and script.
    """
    zone_id = _resolve_zone_id(domain)
    data = {"pattern": pattern, "script": worker}
    result = _request("POST", f"zones/{zone_id}/workers/routes", data)
    return {"id": result.get("result", {}).get("id"), "pattern": pattern, "script": worker}


# ── Entry point ─────────────────────────────────────────────────────


def main() -> None:
    """Start the MCP server."""
    global _settings

    parser = argparse.ArgumentParser(description="Cloudflare MCP Server")
    parser.add_argument(
        "--transport", choices=["stdio", "http"], default="stdio",
        help="MCP transport (stdio or http).",
    )
    parser.add_argument(
        "--host", type=str, default="127.0.0.1",
        help="HTTP host (only used with --transport http).",
    )
    parser.add_argument(
        "--port", type=int, default=8000,
        help="HTTP port (only used with --transport http).",
    )
    parser.add_argument(
        "--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    parser.add_argument(
        "--read-only",
        action="store_true",
        default=None,
        help="Run in read-only mode (hide write tools)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        stream=sys.stderr,
    )

    _settings = Settings(
        cloudflare_transport=args.transport,
        cloudflare_log_level=args.log_level,
    )

    creds = _settings.load_credentials()
    if not creds.get("api_token"):
        logging.error(
            "Missing CLOUDFLARE_API_TOKEN. Set env var or create "
            "~/.config/cloudflare/credentials.json"
        )
        sys.exit(1)

    read_only = args.read_only if args.read_only is not None else _settings.cf_read_only
    if read_only:
        removed = remove_non_read_tools(mcp)
        logging.info("Read-only mode: %d non-read tools removed", removed)

    if args.transport == "http":
        mcp.run(transport="http", host=args.host, port=args.port)
    else:
        mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()
