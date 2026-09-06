"""Utility for getting the cloudflared tunnel URL at runtime."""

import asyncio
import re
from typing import Optional

import aiohttp
from loguru import logger


class TunnelURLProvider:
    """Provider for getting tunnel URLs from cloudflared service."""

    @classmethod
    async def get_tunnel_urls(cls) -> tuple[str, str]:
        """
        Get the tunnel URLs for external access.

        Returns:
            tuple[str, str]: (https_url, wss_url) - Both URLs include full protocol

        Raises:
            ValueError: If no tunnel URL can be determined
        """

        try:
            # Try to get URL from cloudflared metrics
            urls = await cls._get_cloudflared_urls()
            if urls:
                return urls
        except Exception as e:
            logger.warning(f"Failed to get tunnel URL from cloudflared: {e}")

        raise ValueError(
            "No tunnel URL available. Please set BACKEND_API_ENDPOINT environment "
            "variable or ensure cloudflared service is running."
        )

    @classmethod
    async def _get_cloudflared_urls(cls) -> Optional[tuple[str, str]]:
        """
        Query cloudflared metrics endpoint to get the tunnel URLs.

        Returns:
            Optional[tuple[str, str]]: (https_url, wss_url) with full protocols, or None if not found
        """
        try:
            # Try to connect to cloudflared metrics / quicktunnel endpoints
            # Works in both Docker (cloudflared:2000) and Local Windows (127.0.0.1:20241, localhost:2000)
            candidate_endpoints = [
                "http://127.0.0.1:20241/quicktunnel",
                "http://cloudflared:2000/metrics",
                "http://127.0.0.1:20241/metrics",
                "http://localhost:2000/metrics",
            ]

            async with aiohttp.ClientSession() as session:
                for ep in candidate_endpoints:
                    try:
                        async with session.get(
                            ep, timeout=aiohttp.ClientTimeout(total=2)
                        ) as response:
                            if response.status != 200:
                                continue
                            text = await response.text()
                            # Handle quicktunnel JSON response {"hostname":"..."}
                            if "hostname" in text and "{" in text:
                                import json
                                try:
                                    j = json.loads(text)
                                    h = j.get("hostname")
                                    if h:
                                        h = h.replace("https://", "").replace("wss://", "")
                                        return f"https://{h}", f"wss://{h}"
                                except Exception:
                                    pass

                            # Look for the tunnel URL in metrics userHostname
                            match = re.search(r'userHostname="([^"]+)"', text)
                            if match:
                                hostname = match.group(1).replace("https://", "").replace("wss://", "")
                                return f"https://{hostname}", f"wss://{hostname}"

                            # Alternative: Look for trycloudflare.com domain
                            match = re.search(r"([a-z0-9-]+\.trycloudflare\.com)", text)
                            if match:
                                hostname = match.group(1).replace("https://", "").replace("wss://", "")
                                return f"https://{hostname}", f"wss://{hostname}"
                    except Exception:
                        continue

            logger.warning("Could not find active tunnel URL from any cloudflared endpoints")
            return None

        except asyncio.TimeoutError:
            logger.warning("Timeout connecting to cloudflared metrics endpoint")
            return None
        except aiohttp.ClientError as e:
            logger.warning(f"Error connecting to cloudflared: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error getting cloudflared URL: {e}")
            return None
