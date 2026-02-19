# clr-cloudflare-mcp

MCP server for Cloudflare DNS records, cache purging, and Workers routes.

## Tools

| Tool | Description |
|------|-------------|
| `cf_verify_token` | Verify API token validity |
| `cf_list_zones` | List all zones (domains) |
| `cf_get_zone` | Get zone details |
| `cf_list_dns_records` | List DNS records for a domain |
| `cf_add_dns_record` | Create a DNS record |
| `cf_update_dns_record` | Update a DNS record |
| `cf_delete_dns_record` | Delete a DNS record |
| `cf_purge_cache` | Purge cached content |
| `cf_list_routes` | List Workers routes |
| `cf_add_route` | Create a Workers route |

## Configuration

Set `CLOUDFLARE_API_TOKEN` env var or create `~/.config/cloudflare/credentials.json`:

```json
{
  "api_token": "your-token"
}
```

## Usage

```bash
uv run clr-cloudflare-mcp
```
