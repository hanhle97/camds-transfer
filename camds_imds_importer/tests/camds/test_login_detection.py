from __future__ import annotations

from camds_imds_importer.camds.login import is_authenticated, verification_required


class FakeLocator:
    def __init__(self, visible: bool = False, text: str = "") -> None:
        self.visible = visible
        self.text = text
        self.first = self

    async def count(self) -> int:
        return int(self.visible)

    async def is_visible(self) -> bool:
        return self.visible

    async def inner_text(self) -> str:
        return self.text


class FakePage:
    def __init__(self, url: str, body: str = "", visible_selectors: set[str] | None = None) -> None:
        self.url = url
        self.body = body
        self.visible_selectors = visible_selectors or set()

    def get_by_label(self, *_args, **_kwargs):
        return FakeLocator()

    def get_by_placeholder(self, *_args, **_kwargs):
        return FakeLocator()

    def get_by_role(self, role, **_kwargs):
        return FakeLocator(role == "navigation" and "navigation" in self.visible_selectors)

    def locator(self, selector):
        if selector == "body":
            return FakeLocator(True, self.body)
        return FakeLocator(selector in self.visible_selectors)


async def test_verification_detection_from_visible_text() -> None:
    assert await verification_required(FakePage("http://example/#/login", "请完成滑块验证"))


async def test_authenticated_detection_uses_url_and_navigation() -> None:
    assert await is_authenticated(FakePage("http://example/#/home", visible_selectors={"navigation"}))


async def test_authenticated_spa_shell_can_keep_login_hash() -> None:
    assert await is_authenticated(FakePage("http://example/#/login", visible_selectors={"navigation"}))


async def test_login_page_is_not_authenticated() -> None:
    assert not await is_authenticated(FakePage("http://example/#/login"))
