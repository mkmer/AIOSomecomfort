class SomeComfortError(Exception):
    """SomeComfort general error class."""


class ConnectionTimeout(SomeComfortError):
    """SomeComfort Connection Timeout Error."""


class ConnectionError(SomeComfortError):
    """SomeComfort Connection Error."""


class AuthError(SomeComfortError):
    """SomeComfort Authentication Error."""


class APIError(SomeComfortError):
    """SomeComfort General API error."""


class APIRateLimited(SomeComfortError):
    """SomeComfort API Rate limited."""


class SessionTimedOut(SomeComfortError):
    """SomeComfort Session Timeout."""


class ServiceUnavailable(SomeComfortError):
    """SomeComfort Service Unavailable."""


class UnexpectedResponse(SomeComfortError):
    """SomeComfort responded with incorrect type."""


class UnauthorizedError(SomeComfortError):
    """Unauthroized response from SomeComfort."""
