"""Request-scoped access to the configured studio library."""
from fastapi import Request


def library_for(request: Request):
    return request.app.state.library
