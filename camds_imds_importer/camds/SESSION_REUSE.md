# Where the CAMDS session lives, and why it was asked for twice

## What CAMDS uses

Read from `.runtime/camds_storage_state.json` on 2026-09-06:

| Kind | Name | Expires |
|---|---|---|
| cookie | `SESSION` | `-1` (browser session) |
| cookie | `catarc_infoSysSid` | `-1` |
| cookie | `language` | `-1` |
| localStorage | `vuex`, `lang`, `vueVersion` | — |

There is no bearer token: no `Authorization` header appears on any of the 388
API calls across the seven HAR captures. The session travels as a cookie, and
`Access-Control-Allow-Credentials: true` on every response says the same.

Playwright's `storage_state` holds exactly these two kinds - cookies and
localStorage - so one file is enough to reuse a sign-in. `expires: -1` marks
them as browser-session cookies; the file keeps them anyway, and how long they
remain usable is CAMDS's decision, not ours.

## Why signing in did not carry over

`storage_state(path=...)` was only ever called from the scripted login. The
CAPTCHA means the operator normally types the login into the headed window
instead, and that path saved nothing. The file on disk was a day old while the
app asked for a sign-in on every launch.

The state is now written whenever the live page is *seen* authenticated,
however that came about, and again on a clean shutdown in case CAMDS handed out
a fresh cookie during the session. It is never written for a session that
expired or was never established, so one lapse cannot throw away a good file.

## What the file is not

It is not evidence of being signed in. `session_status` reads the live page on
every poll and the app reports that, never the file. A stale file costs one
sign-in; it can never make the app claim a session it does not have.

## Handling

`.runtime/` is gitignored. The file holds a live CAMDS session for as long as
the server honours it - treat it like a password: it is enough to act as the
signed-in user. It holds no username or password.
