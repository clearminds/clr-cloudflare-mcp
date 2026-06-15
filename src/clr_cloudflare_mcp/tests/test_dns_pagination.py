"""Tests for cf_list_dns_records pagination and filtering."""

import urllib.parse

from clr_cloudflare_mcp import server

# 32 hex chars: _resolve_zone_id returns this as-is, so no extra _request call.
ZONE_ID = "0123456789abcdef0123456789abcdef"


def _parse(endpoint):
    path, _, qs = endpoint.partition("?")
    return path, dict(urllib.parse.parse_qsl(qs))


def make_fake(pages):
    """Build a fake _request returning one page of records per call.

    pages: list of record-lists, 1-indexed by the ``page`` query param.
    """
    calls = []

    def fake_request(method, endpoint, data=None):
        path, params = _parse(endpoint)
        calls.append((method, path, params))
        page = int(params.get("page", "1"))
        records = pages[page - 1]
        return {
            "success": True,
            "result": records,
            "result_info": {"page": page, "total_pages": len(pages)},
        }

    fake_request.calls = calls
    return fake_request


def test_request_all_pages_concatenates_all_pages(monkeypatch):
    pages = [[{"id": "a"}], [{"id": "b"}], [{"id": "c"}]]
    fake = make_fake(pages)
    monkeypatch.setattr(server, "_request", fake)

    out = server._request_all_pages("zones/X/dns_records")

    assert [r["id"] for r in out] == ["a", "b", "c"]
    assert len(fake.calls) == 3
    assert fake.calls[0][2]["per_page"] == "100"
    assert [c[2]["page"] for c in fake.calls] == ["1", "2", "3"]


def test_list_dns_records_includes_records_from_later_pages(monkeypatch):
    page1 = [
        {"id": "1", "type": "A", "name": "a.example.com",
         "content": "1.1.1.1", "proxied": False, "ttl": 1}
    ]
    page2 = [
        {"id": "2", "type": "CNAME", "name": "s3.example.com",
         "content": "core.example.com", "proxied": False, "ttl": 300}
    ]
    monkeypatch.setattr(server, "_request", make_fake([page1, page2]))

    out = server.cf_list_dns_records.fn(ZONE_ID)

    names = {r["name"] for r in out}
    assert "s3.example.com" in names
    cname = next(r for r in out if r["name"] == "s3.example.com")
    assert cname["id"] == "2"
    assert cname["type"] == "CNAME"


def test_name_filter_passed_through(monkeypatch):
    fake = make_fake([[]])
    monkeypatch.setattr(server, "_request", fake)

    server.cf_list_dns_records.fn(ZONE_ID, name="s3.example.com")

    assert fake.calls[0][2]["name"] == "s3.example.com"


def test_type_filter_uppercased(monkeypatch):
    fake = make_fake([[]])
    monkeypatch.setattr(server, "_request", fake)

    server.cf_list_dns_records.fn(ZONE_ID, record_type="cname")

    assert fake.calls[0][2]["type"] == "CNAME"
