# CAMDS login discovery

Observed live on 2026-09-05 at https://catarc.camds.org.cn/#/login.
Title: 中国汽车材料数据系统. The page successfully rendered after initial load.

| Control | Observed locator | Verification |
| --- | --- | --- |
| Username | `input[name="username"]` | Exactly one match; placeholder 用户名 |
| Password | `input[name="password"]` | Exactly one match; type password; placeholder 密码 |
| Login | `get_by_role("button", name="登录", exact=True)` | Exactly one match; CSS fallback `button.login-btn` observed |
| Language | `input[placeholder="请选择"][readonly]` | Observed input displaying 中文; options not inspected |
| Manual slider verification | `.drag_verify` | Visible text 请拖住滑块，拖动到最右边 |

The existing username, password and login strategies contain matching locators.
The existing VERIFICATION CSS fallbacks do not include `.drag_verify`; add this
observed selector when updating verification detection. This is a slider, not a
text CAPTCHA input. Its completion and post-completion state were not tested.

Do not use a generic `input[type="text"]` alone to identify username: the language
selector is also a text input. Prefer the observed stable name attributes.
Do not infer authentication from the initial empty body while the SPA loads.

No credentials were entered, no login was submitted, and no MDS data was changed.
Authenticated navigation, search, component/material/substance controls, draft
actions and read-back selectors remain unverified and require a signed-in session.
