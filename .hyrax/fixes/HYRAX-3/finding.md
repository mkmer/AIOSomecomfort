# Location assigned outside try block — NameError on KeyError swallows exception and crashes

**Tool:** `mini_audit`
**Severity:** high
**Category:** correctness
**Location:** `aiosomecomfort/__init__.py:271`

## What's wrong

The assignment `self._locations[location.locationid] = location` is outside the `try/except KeyError` block (line 264–270). When `Location.from_api_response` raises a `KeyError`, the `except` block logs the error but execution falls through to line 271, where `location` is not bound (it was never assigned). This raises an `UnboundLocalError` that is unhandled and propagates out of `discover()`, killing the discovery of all subsequent locations instead of just skipping the bad one. Fix: move line 271 inside the `try` block, or use an `else:` clause on the `try`.
