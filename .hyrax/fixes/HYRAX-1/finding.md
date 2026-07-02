# `_convert_errors` wrapper is not async — decorated coroutines are called synchronously and their result is never awaited

**Tool:** `mini_audit`
**Severity:** critical
**Category:** correctness
**Location:** `aiosomecomfort/__init__.py:19`

## What's wrong

`_convert_errors` returns a plain (non-async) `wrapper` function. When it decorates `async def login(...)` or `async def discover(...)`, calling the wrapper returns a coroutine object (the result of `fn(*args, **kwargs)`) — it does NOT await it. The `try/except aiohttp.ClientError` therefore never fires for async errors, and the returned coroutine object is silently discarded unless the caller somehow awaits the wrapper's return value. The wrapper must be `async def wrapper` and must `return await fn(...)`. This is a correctness bug that also means the intended error-translation safety net is entirely absent for all decorated methods.
