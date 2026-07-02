# Credentials sent in URL query string (logged in plaintext via debug logging)

**Tool:** `mini_audit`
**Severity:** high
**Category:** security
**Location:** `aiosomecomfort/__init__.py:86`

## What's wrong

At line 81, `username` and `password` are URL-encoded into the GET query string: `url = URL(f"{url}?{urllib.urlencode(params)}", encoded=True)`. This URL — containing the plaintext password — is then passed to `self._session.post(...)`. Line 89 does `_LOG.debug("Login Response %s", await resp.text())` and line 92 does `_LOG.debug("Cookies: %s", cookies)`. More critically, aiohttp will include the full URL (with credentials) in its own access logs and in the `TraceConfig` hooks wired in `test.py` (lines 256–258), which explicitly log request headers and params at DEBUG level. Passwords in URLs also appear in server access logs, proxy logs, and HTTP `Referer` headers. Fix: send credentials in the POST body (not in the URL), or at minimum suppress credential fields from debug logging.
