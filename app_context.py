"""Request-scoped access to the configured studio library."""


def library_for(request):
    return request.app.state.library
