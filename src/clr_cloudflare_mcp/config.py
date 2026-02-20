"""Configuration for Cloudflare MCP Server.

Standard credential loading pattern for Clearminds MCP servers:
1. Load from environment variables FIRST (base/fallback)
2. Override with ~/.config/cloudflare/credentials.json (takes priority)
"""

import json
import logging
from pathlib import Path
from typing import Any

from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)

CREDS_PATH = Path.home() / ".config" / "cloudflare" / "credentials.json"
TOKEN_PATH = Path.home() / ".config" / "cloudflare" / "token"


class Settings(BaseSettings):
    """Settings loaded from credentials file or environment variables.

    Priority order:
    1. Environment variables (CLOUDFLARE_API_TOKEN) - base/fallback
    2. ~/.config/cloudflare/credentials.json or token file - takes priority

    Attributes:
        cloudflare_api_token: Cloudflare API bearer token.
        cloudflare_transport: MCP transport protocol (stdio or sse).
        cloudflare_log_level: Logging verbosity level.
    """

    cloudflare_api_token: str = ""

    cloudflare_transport: str = "stdio"
    cloudflare_log_level: str = "INFO"

    model_config = {"env_prefix": ""}

    def load_credentials(self) -> dict[str, Any]:
        """Load credentials with env-first, config-file-override pattern.

        Returns:
            A dictionary containing the loaded credentials (e.g. ``api_token``).
        """
        creds: dict[str, Any] = {}

        # 1. FIRST: Load from environment variables (base/fallback)
        if self.cloudflare_api_token:
            creds["api_token"] = self.cloudflare_api_token

        # 2. THEN: Override with credentials file (takes priority)
        if CREDS_PATH.exists():
            try:
                file_creds: dict[str, Any] = json.loads(CREDS_PATH.read_text())
                if "api_token" in file_creds:
                    creds["api_token"] = file_creds["api_token"]
                logger.info(f"Loaded credentials from {CREDS_PATH}")
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning(f"Failed to load {CREDS_PATH}: {e}")
        elif TOKEN_PATH.exists():
            try:
                token = TOKEN_PATH.read_text().strip()
                if token:
                    creds["api_token"] = token
                    logger.info(f"Loaded token from {TOKEN_PATH}")
            except OSError as e:
                logger.warning(f"Failed to load {TOKEN_PATH}: {e}")

        if not self._has_required_creds(creds):
            logger.warning(
                "No credentials configured. Set CLOUDFLARE_API_TOKEN env var "
                f"or create {CREDS_PATH}"
            )

        return creds

    def _has_required_creds(self, creds: dict[str, Any]) -> bool:
        """Check whether the required ``api_token`` credential is present."""
        return bool(creds.get("api_token"))
