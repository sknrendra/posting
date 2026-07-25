from fastapi.templating import Jinja2Templates


def _csrf_context(request):
    return {"csrf_token": getattr(request.state, "csrf_token", "")}


templates = Jinja2Templates(directory="app/templates", context_processors=[_csrf_context])
