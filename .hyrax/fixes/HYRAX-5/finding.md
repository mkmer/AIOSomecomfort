# `_get_locations` silently ignores non-200 / non-JSON responses — authentication failures pass undetected

**Tool:** `mini_audit`
**Severity:** high
**Category:** correctness
**Location:** `aiosomecomfort/__init__.py:196`

## What's wrong

The loop on lines 199–208 posts to the location endpoint and only acts on the response if `resp.content_type == "application/json"` (line 202). Any other status (401, 403, 500, redirects that strip the cookie) is silently ignored. If the session has expired, this returns an empty list or `None` and `discover()` silently succeeds with no locations — no error is raised. Unlike `_request_json`, there is no status-code checking here. Fix: check `resp.status` and raise appropriate errors (e.g. `UnauthorizedError` on 401/403) as done in `_request_json`.
