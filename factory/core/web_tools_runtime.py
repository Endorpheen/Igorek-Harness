"""Task-local web credentials stay behind the owning runtime callback."""
from contextlib import contextmanager
from contextvars import ContextVar

_provider = ContextVar("web_tools_provider", default=None)


def active_web_tools():
    return _provider.get()


@contextmanager
def scoped_web_tools(provider):
    token = _provider.set(provider)
    try:
        yield
    finally:
        _provider.reset(token)
