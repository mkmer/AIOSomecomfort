# `URL(resp.host)` builds a malformed URL — cookies may be stored/matched against wrong origin

**Tool:** `mini_audit`
**Severity:** high
**Category:** correctness
**Location:** `aiosomecomfort/__init__.py:95`

## What's wrong

`resp.host` is just the hostname string (e.g. `"mytotalconnectcomfort.com"`), not a full URL. Passing it to `yarl.URL()` without a scheme creates a relative URL (`URL("mytotalconnectcomfort.com")` is interpreted as a path, not a host). aiohttp's `CookieJar.update_cookies` uses the `response_url` to scope the cookie; a malformed URL means the cookie is either stored with the wrong scope or raises an error. The same bug appears on lines 163 and 208. Fix: use `URL(f"https://{resp.host}")` or `resp.url` instead.
