from starlette.middleware.base import BaseHTTPMiddleware

from app.config import settings
from app.utils.security import generate_token

CSRF_COOKIE_NAME = "csrf_token"


class CsrfCookieMiddleware(BaseHTTPMiddleware):
    """Double-submit-cookie CSRF protection: ensures every request carries a
    stable csrf_token cookie, exposed to templates via request.state.csrf_token
    so forms can render it as a hidden field. See dependencies.verify_csrf."""

    async def dispatch(self, request, call_next):
        token = request.cookies.get(CSRF_COOKIE_NAME)
        needs_cookie = not token
        if needs_cookie:
            token = generate_token(16)
        request.state.csrf_token = token

        response = await call_next(request)

        if needs_cookie:
            response.set_cookie(
                CSRF_COOKIE_NAME,
                token,
                httponly=True,
                samesite="lax",
                secure=settings.session_cookie_secure,
                max_age=60 * 60 * 24 * 30,
            )
        return response
