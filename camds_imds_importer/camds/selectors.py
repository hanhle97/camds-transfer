from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SelectorStrategy:
    role: str | None = None
    names: tuple[str, ...] = ()
    labels: tuple[str, ...] = ()
    placeholders: tuple[str, ...] = ()
    selectors: tuple[str, ...] = ()


LOGIN_USERNAME = SelectorStrategy(
    labels=("Username", "User name", "用户名", "账号"),
    placeholders=("Username", "User name", "用户名", "请输入用户名"),
    selectors=("input[name='username']", "input[name='userName']", "input[type='text']"),
)
LOGIN_PASSWORD = SelectorStrategy(
    labels=("Password", "密码"),
    placeholders=("Password", "密码", "请输入密码"),
    selectors=("input[name='password']", "input[type='password']"),
)
LOGIN_BUTTON = SelectorStrategy(
    role="button",
    names=("Login", "Log in", "登录"),
    selectors=("button[type='submit']", "input[type='submit']"),
)
VERIFICATION = SelectorStrategy(
    names=("Captcha", "Verification", "验证码", "滑块验证", "安全验证"),
    selectors=(".drag_verify", "[class*='captcha']", "[class*='slider']", "[id*='captcha']", "iframe[src*='captcha']"),
)
VERIFICATION_INPUT = SelectorStrategy(
    labels=("Captcha", "Verification code", "验证码", "校验码"),
    placeholders=("Captcha", "验证码", "请输入验证码"),
    selectors=("input[name*='captcha' i]", "input[name*='verify' i]", "input[placeholder*='验证码']"),
)
VERIFICATION_SUBMIT = SelectorStrategy(
    role="button",
    names=("Verify", "确认", "确定", "提交"),
    selectors=("button[type='submit']",),
)
AUTHENTICATED = SelectorStrategy(
    role="navigation",
    names=("Home", "主页", "首页", "MDS"),
    selectors=("nav", "[class*='navigation']", "[class*='sidebar']"),
)
