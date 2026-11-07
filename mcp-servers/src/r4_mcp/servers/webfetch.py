"""
Web Fetch MCP Server (M-103).

Provides tools for fetching web content with security restrictions.

Security features:
- URL allowlist by domain
- Content size limits
- Timeout limits
- No credentials/cookies
"""

from __future__ import annotations

import logging
import re
from typing import Optional
from urllib.parse import urlparse

from ..base import MCPServer, Tool, ToolResult

logger = logging.getLogger(__name__)


class WebFetchServer(MCPServer):
    """
    MCP server for web content fetching.

    Provides safe HTTP GET requests with domain restrictions.
    """

    name = "webfetch"
    version = "1.0.0"

    def __init__(
        self,
        allowed_domains: Optional[list[str]] = None,
        blocked_domains: Optional[list[str]] = None,
        max_content_length: int = 1024 * 1024,  # 1MB
        timeout_seconds: int = 30,
        user_agent: str = "r4-mcp-webfetch/1.0",
    ):
        """
        Initialize web fetch server.

        Args:
            allowed_domains: If set, only these domains are allowed
            blocked_domains: Domains to block (applied after allowed_domains)
            max_content_length: Maximum response size in bytes
            timeout_seconds: Request timeout
            user_agent: User-Agent header
        """
        super().__init__()
        self.allowed_domains = allowed_domains or []
        self.blocked_domains = blocked_domains or [
            "localhost",
            "127.0.0.1",
            "0.0.0.0",
            "::1",
            "169.254.*",  # Link-local
            "10.*",  # Private
            "192.168.*",  # Private
            "172.16.*",  # Private
        ]
        self.max_content_length = max_content_length
        self.timeout_seconds = timeout_seconds
        self.user_agent = user_agent

    def _validate_url(self, url: str) -> tuple[bool, Optional[str]]:
        """Validate URL against security rules."""
        try:
            parsed = urlparse(url)
        except Exception:
            return False, "Invalid URL format"

        # Only HTTP(S)
        if parsed.scheme not in ("http", "https"):
            return False, f"Scheme not allowed: {parsed.scheme}"

        # Check domain
        domain = parsed.hostname or ""

        # Check blocked domains (with wildcard support)
        for blocked in self.blocked_domains:
            if blocked.endswith("*"):
                prefix = blocked[:-1]
                if domain.startswith(prefix):
                    return False, f"Domain blocked: {domain}"
            elif domain == blocked or domain.endswith(f".{blocked}"):
                return False, f"Domain blocked: {domain}"

        # Check allowed domains if specified
        if self.allowed_domains:
            allowed = False
            for allow in self.allowed_domains:
                if allow.startswith("*."):
                    # Wildcard subdomain
                    suffix = allow[1:]  # .example.com
                    if domain.endswith(suffix) or domain == allow[2:]:
                        allowed = True
                        break
                elif domain == allow:
                    allowed = True
                    break
            if not allowed:
                return False, f"Domain not in allowlist: {domain}"

        return True, None

    @Tool(
        name="fetch",
        description="Fetch content from a URL",
    )
    async def fetch(
        self,
        url: str,
        headers: Optional[dict[str, str]] = None,
    ) -> ToolResult:
        """
        Fetch content from URL.

        Args:
            url: URL to fetch
            headers: Additional headers (optional)
        """
        # Validate URL
        valid, error = self._validate_url(url)
        if not valid:
            return ToolResult.error(f"URL validation failed: {error}")

        try:
            import aiohttp
        except ImportError:
            return ToolResult.error("aiohttp not installed. Install with: pip install aiohttp")

        request_headers = {"User-Agent": self.user_agent}
        if headers:
            # Don't allow overriding certain headers
            for key in ["Cookie", "Authorization"]:
                headers.pop(key, None)
            request_headers.update(headers)

        try:
            timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, headers=request_headers) as response:
                    # Check content length
                    content_length = response.content_length
                    if content_length and content_length > self.max_content_length:
                        return ToolResult.error(
                            f"Content too large: {content_length} bytes "
                            f"(max: {self.max_content_length})"
                        )

                    # Read content with size limit
                    content = await response.content.read(self.max_content_length + 1)
                    if len(content) > self.max_content_length:
                        return ToolResult.error(
                            f"Content exceeds max size: {self.max_content_length} bytes"
                        )

                    # Determine content type
                    content_type = response.content_type or "text/plain"

                    # Try to decode as text
                    try:
                        text = content.decode("utf-8")
                    except UnicodeDecodeError:
                        text = content.decode("latin-1")

                    return ToolResult.success(
                        {
                            "url": str(response.url),
                            "status": response.status,
                            "content_type": content_type,
                            "content_length": len(content),
                            "content": text,
                        }
                    )

        except aiohttp.ClientError as e:
            return ToolResult.error(f"HTTP error: {e}")
        except Exception as e:
            logger.exception("Fetch failed")
            return ToolResult.error(f"Fetch failed: {e}")

    @Tool(
        name="fetch_json",
        description="Fetch and parse JSON from a URL",
    )
    async def fetch_json(
        self,
        url: str,
        headers: Optional[dict[str, str]] = None,
    ) -> ToolResult:
        """
        Fetch and parse JSON from URL.

        Args:
            url: URL to fetch
            headers: Additional headers (optional)
        """
        result = await self.fetch(url, headers)
        if result.isError:
            return result

        content = result.content[0].get("result", {})
        raw_content = content.get("content", "")

        try:
            import json

            data = json.loads(raw_content)
            return ToolResult.success(data)
        except Exception as e:
            return ToolResult.error(f"JSON parse error: {e}")

    @Tool(
        name="extract_text",
        description="Fetch URL and extract text content (strips HTML)",
    )
    async def extract_text(
        self,
        url: str,
    ) -> ToolResult:
        """
        Fetch URL and extract plain text.

        Args:
            url: URL to fetch
        """
        result = await self.fetch(url)
        if result.isError:
            return result

        content = result.content[0].get("result", {})
        raw_content = content.get("content", "")
        content_type = content.get("content_type", "")

        # If HTML, strip tags
        if "html" in content_type.lower():
            try:
                from html.parser import HTMLParser

                class TextExtractor(HTMLParser):
                    def __init__(self):
                        super().__init__()
                        self.text = []
                        self.skip_tags = {"script", "style", "head", "meta"}
                        self.current_tag = None

                    def handle_starttag(self, tag, attrs):
                        self.current_tag = tag

                    def handle_endtag(self, tag):
                        self.current_tag = None

                    def handle_data(self, data):
                        if self.current_tag not in self.skip_tags:
                            text = data.strip()
                            if text:
                                self.text.append(text)

                extractor = TextExtractor()
                extractor.feed(raw_content)
                text = "\n".join(extractor.text)

            except Exception:
                # Fallback: simple tag removal
                text = re.sub(r"<[^>]+>", " ", raw_content)
                text = re.sub(r"\s+", " ", text).strip()
        else:
            text = raw_content

        return ToolResult.success(
            {
                "url": content.get("url", url),
                "text": text,
                "length": len(text),
            }
        )


def create_server(
    allowed_domains: Optional[list[str]] = None,
    blocked_domains: Optional[list[str]] = None,
    max_content_length: int = 1024 * 1024,
    timeout_seconds: int = 30,
) -> WebFetchServer:
    """Create a web fetch MCP server instance."""
    return WebFetchServer(
        allowed_domains=allowed_domains,
        blocked_domains=blocked_domains,
        max_content_length=max_content_length,
        timeout_seconds=timeout_seconds,
    )
