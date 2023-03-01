class SomeComfortError(Exception):
    """SomeComfort general error class."""

    pass


class ConnectionTimeout(SomeComfortError):
    """SomeComfort Connection Timeout Error."""

    pass


class ConnectionError(SomeComfortError):
    """SomeComfort Connection Error."""

    pass


class AuthError(SomeComfortError):
    """SomeComfort Authentication Error."""

    pass


class APIError(SomeComfortError):
    """SomeComfort General API error."""

    pass


class APIRateLimited(SomeComfortError):
    """SomeComfort API Rate limited."""

    pass


class SessionTimedOut(SomeComfortError):
    """SomeComfort Session Timeout."""

    pass


class ServiceUnavailable(SomeComfortError):
    """SomeComfort Service Unavailable."""

    pass
