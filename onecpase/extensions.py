from flask_caching import Cache
from flask_mail import Mail
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

cache   = Cache()
mail    = Mail()
csrf    = CSRFProtect()


def _rate_limit_key() -> str:
    """Count signed-in staff one by one; count everyone else by address.

    The whole gym reaches the app from one public address, so counting by address alone made the
    entire team share a single allowance (300 requests a day between them). A signed-in person is
    identified by their account, so one busy user can no longer lock everyone else out.
    """
    try:
        from flask import session

        user_id = session.get("user_id")
        if user_id:
            return f"user:{user_id}"
    except RuntimeError:  # no request context
        pass
    return get_remote_address()


# Allowances are per signed-in user (or per address for anonymous visitors). Login, one-time-code,
# public-form and other sensitive routes keep their own much stricter limits via @limiter.limit.
limiter = Limiter(key_func=_rate_limit_key, default_limits=["5000 per day", "1000 per hour"])
