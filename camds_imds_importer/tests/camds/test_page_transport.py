r"""The API transport that cannot reach a different place than the browser.

`context.request` shares the cookie jar but is still a Node HTTP client: it
resolves DNS and picks a proxy itself. On some networks it could not reach
CAMDS at all while the signed-in window beside it worked. A fetch evaluated in
a page is the browser, so it cannot diverge.
"""
import json

import pytest

from camds_imds_importer.camds.api import CamdsApi, CamdsApiError
from camds_imds_importer.camds.page_transport import PageTransport


class Page:
    """Records what the page was asked to evaluate, and answers as fetch does."""

    def __init__(self, status=200, text='{"respCode":"0","ok":true,"data":{"records":[]}}'):
        self.status, self.text, self.calls = status, text, []
        self.context = None

    async def evaluate(self, script, argument):
        self.calls.append(argument)
        return {"status": self.status, "text": self.text}


async def test_a_call_carries_the_session_because_it_is_same_origin():
    page = Page()
    await CamdsApi(PageTransport(page)).find_material(name="x")
    sent = page.calls[0]
    assert sent["method"] == "POST"
    assert sent["url"].startswith("https://catarc.camds.org.cn/api/")
    assert json.loads(sent["body"])["name"] == "x"


async def test_headers_the_browser_owns_are_not_fought_over():
    """A page cannot set Origin or Referer, and fetch sets them correctly
    anyway. Sending them would be refused, not honoured."""
    page = Page()
    await CamdsApi(PageTransport(page)).find_material(name="x")
    headers = page.calls[0]["headers"]
    for forbidden in ("Origin", "Referer", "Accept-Encoding", "Content-Length"):
        assert forbidden not in headers, forbidden
    assert headers["Content-Type"] == "application/json"


async def test_the_body_is_returned_as_text_so_a_login_page_is_recognised():
    """An expired session answers with HTML. Decoding in the page would turn
    that into a parse error instead of the diagnosis CamdsApi gives."""
    page = Page(status=200, text="<html>login</html>")
    with pytest.raises(CamdsApiError, match="session may have expired"):
        await CamdsApi(PageTransport(page)).find_material(name="x")


async def test_a_refusal_is_reported_with_what_camds_said():
    page = Page(status=401, text="")
    with pytest.raises(CamdsApiError, match="HTTP 401"):
        await CamdsApi(PageTransport(page)).find_material(name="x")


async def test_query_parameters_reach_the_url():
    """loadNodeDate and friends address the node in the query string."""
    page = Page(text='{"respCode":"0","ok":true,"data":{"id":"CA_21_1"}}')
    await CamdsApi(PageTransport(page)).load_tree("CA_5_1")
    assert "mdsId=CA_5_1" in page.calls[0]["url"]


async def test_saving_the_session_asks_the_context_that_holds_the_cookies(tmp_path):
    page = Page()
    saved = []

    class Context:
        async def storage_state(self, path):
            saved.append(path)

    page.context = Context()
    await PageTransport(page).storage_state(tmp_path / "state.json")
    assert saved == [tmp_path / "state.json"]
