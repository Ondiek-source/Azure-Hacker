"""
Network policy — URL validation, SSRF prevention, allow/deny rules.

Validates initial URLs AND every redirect hop.  Resolves DNS to block
private IPs and prevent rebinding attacks.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import socket
from typing import List, Optional
from urllib.parse import urlparse

from worker.exceptions import FatalError, SSRFError

logger = logging.getLogger(__name__)

PRIVATE_NETWORKS: List[ipaddress.IPv4Network | ipaddress.IPv6Network] = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]


class NetworkPolicy:
    """
    URL validation, SSRF prevention, allow/deny rules.

    Validates initial URLs AND every redirect hop.
    Resolves DNS to block private IPs and prevent rebinding attacks.

    Args:
        allowlist:  Substrings that must appear in the URL (any match passes).
                    If empty, all URLs pass the allowlist check.
        denylist:   Substrings that, if found in the URL, cause rejection.
        ssrf_check: Whether to resolve DNS and block private IPs.
    """

    def __init__(
        self,
        allowlist: Optional[List[str]] = None,
        denylist: Optional[List[str]] = None,
        ssrf_check: bool = True,
    ) -> None:
        self.allowlist = allowlist or []
        self.denylist = denylist or []
        self.ssrf_check = ssrf_check

    def validate(self, url: str) -> None:
        """Run full validation: scheme, allow/deny, SSRF.

        Args:
            url: Absolute URL to validate.

        Raises:
            FatalError:  If the URL violates scheme, allowlist, or denylist rules.
            SSRFError:   If the URL resolves to a private/loopback address.
        """
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            raise FatalError(f"Invalid URL: {url}")
        if parsed.scheme not in ("http", "https"):
            raise FatalError(f"Unsupported scheme: {parsed.scheme}")
        for deny in self.denylist:
            if deny in url:
                raise FatalError(f"URL denied: {url}")
        if self.allowlist:
            if not any(allow in url for allow in self.allowlist):
                raise FatalError(f"URL not in allowlist: {url}")
        if self.ssrf_check:
            self._check_ssrf(url)

    def _check_ssrf(self, url: str) -> None:
        """Resolve DNS and verify no private/loopback IPs.

        Args:
            url: URL whose hostname will be resolved.

        Raises:
            SSRFError: If the hostname resolves to a private IP,
                        is localhost (without ``ALLOW_LOCALHOST=1``),
                        or DNS resolution fails.
        """
        hostname = urlparse(url).hostname
        if not hostname:
            raise SSRFError(f"No hostname: {url}")
        if hostname in ("localhost", "0.0.0.0"):
            if os.environ.get("ALLOW_LOCALHOST") != "1":
                raise SSRFError(f"Localhost blocked: {url}")
            return
        try:
            infos = socket.getaddrinfo(hostname, None)
        except socket.gaierror:
            raise SSRFError(f"DNS failed: {hostname}")
        for info in infos:
            ip_str = info[4][0]
            try:
                addr = ipaddress.ip_address(ip_str)
            except ValueError:
                raise SSRFError(f"Unparseable IP: {ip_str}")
            for net in PRIVATE_NETWORKS:
                if addr in net:
                    raise SSRFError(f"Private IP {addr} from {hostname}")
