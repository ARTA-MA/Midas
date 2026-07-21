"""Offline stand-in for httpx used by the stress tests when the real
package is unavailable. Every network call fails fast with ConnectError,
which is exactly what the engine must survive gracefully."""


class HTTPError(Exception):
    pass


class ConnectError(HTTPError):
    pass


class TimeoutException(HTTPError):
    pass


class Response:
    status_code = 599
    text = ""
    content = b""

    def json(self):
        raise ValueError("no body")


def _fail(*_a, **_k):
    raise ConnectError("stress-test sandbox: network disabled")


get = post = put = delete = head = stream = _fail


class Client:
    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    get = post = put = delete = head = staticmethod(_fail)
