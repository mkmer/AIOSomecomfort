class SomeComfortError(Exception):
    pass


class ConnectionTimeout(SomeComfortError):
    pass


class ConnectionError(SomeComfortError):
    pass


class AuthError(SomeComfortError):
    pass


class APIError(SomeComfortError):
    pass


class APIRateLimited(SomeComfortError):
    pass


class SessionTimedOut(SomeComfortError):
    pass


class ServiceUnavailable(SomeComfortError):
    pass
