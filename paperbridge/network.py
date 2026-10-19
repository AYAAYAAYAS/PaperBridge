"""HTTPS helpers that survive broken Windows/OpenSSL certificate defaults."""
from __future__ import annotations

import ssl
import urllib.error
import urllib.request

import certifi


def ssl_context() -> ssl.SSLContext:
    """Use the certificate bundle installed with the application environment."""
    return ssl.create_default_context(cafile=certifi.where())


def _can_retry_direct(exc: urllib.error.URLError) -> bool:
    reason = exc.reason
    return isinstance(reason, (FileNotFoundError, ssl.SSLEOFError))


def urlopen(request, timeout=30):
    """Open with system networking, falling back to direct for a broken proxy."""
    context = ssl_context()
    try:
        return urllib.request.urlopen(request, timeout=timeout, context=context)
    except urllib.error.URLError as exc:
        if not _can_retry_direct(exc):
            raise
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPSHandler(context=context),
        )
        return opener.open(request, timeout=timeout)


def force_direct_httpx_for_babeldoc() -> None:
    """Make BabelDOC's isolated cloud client ignore invalid system TLS paths."""
    from babeldoc.translator import translator

    original = translator.httpx.Client
    if getattr(original, "_paperbridge_direct", False):
        return

    class DirectClient(original):
        _paperbridge_direct = True

        def __init__(self, *args, **kwargs):
            kwargs.setdefault("trust_env", False)
            kwargs.setdefault("verify", ssl_context())
            super().__init__(*args, **kwargs)

    translator.httpx.Client = DirectClient
