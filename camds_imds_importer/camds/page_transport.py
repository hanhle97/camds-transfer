r"""Send CAMDS API calls from inside the browser page.

`context.request` looked like the answer: same cookie jar, same context. It is
still a Node HTTP client, and on some networks it could not reach CAMDS at all

    APIRequestContext.post: getaddrinfo ENOTFOUND catarc.camds.org.cn

while the signed-in window beside it was working. Playwright's request contexts
resolve DNS and choose a proxy themselves; Chromium takes both from the system.
Nothing about which context the request object comes from changes that.

A `fetch()` evaluated in a page cannot diverge: it is the browser's own stack,
so it uses whatever DNS, proxy and TLS trust let the operator sign in, and
sends the session cookie because it is same-origin. The page is CAMDS's own
origin and never shown - the visible window is the operator's, and closing it
must not stop an import.

The object presented here is the small part of `APIRequestContext` that
`CamdsApi` uses, so neither the client nor its tests can tell the difference.
"""
from __future__ import annotations

import json
from typing import Any

# Runs in the page. Returns the status and the body as text, so the caller
# decodes exactly as it would an HTTP response - a CAMDS error page is not JSON,
# and that difference is how an expired session is recognised.
FETCH = """
async ({url, method, body, headers, timeoutMs}) => {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
        const response = await fetch(url, {
            method,
            headers,
            body: body === null ? undefined : body,
            credentials: 'include',
            signal: controller.signal,
        });
        return {status: response.status, text: await response.text()};
    } finally {
        clearTimeout(timer);
    }
}
"""


class PageResponse:
    """What CamdsApi reads off a response: a status and a decoded body."""

    def __init__(self, status: int, text: str) -> None:
        self.status = status
        self._text = text

    async def json(self) -> Any:
        return json.loads(self._text)

    async def text(self) -> str:
        return self._text


def _with_params(url: str, params: dict | None) -> str:
    if not params:
        return url
    from urllib.parse import urlencode
    return url + ("&" if "?" in url else "?") + urlencode(params)


class PageTransport:
    """`APIRequestContext`'s post/get, evaluated in a page instead."""

    def __init__(self, page) -> None:
        self.page = page

    async def _send(self, method: str, url: str, params=None, data=None, headers=None,
                    timeout: float | None = None) -> PageResponse:
        sent = dict(headers or {})
        # fetch sets these itself, and a page cannot override them.
        for forbidden in ("Origin", "Referer", "Accept-Encoding", "Content-Length"):
            sent.pop(forbidden, None)
        body = None if data is None else json.dumps(data, ensure_ascii=False)
        if body is not None:
            sent.setdefault("Content-Type", "application/json")
        result = await self.page.evaluate(FETCH, {
            "url": _with_params(url, params),
            "method": method,
            "body": body,
            "headers": sent,
            "timeoutMs": int(timeout or 60_000),
        })
        return PageResponse(int(result["status"]), str(result["text"]))

    async def post(self, url, params=None, data=None, headers=None, timeout=None):
        return await self._send("POST", url, params, data, headers, timeout)

    async def get(self, url, params=None, headers=None, timeout=None):
        return await self._send("GET", url, params, None, headers, timeout)

    async def storage_state(self, path):
        """The cookies live in the context, which is what can save them."""
        return await self.page.context.storage_state(path=path)
