from flask_caching import Cache
from flask_mail import Mail
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

cache   = Cache()
mail    = Mail()
csrf    = CSRFProtect()
limiter = Limiter(key_func=get_remote_address, default_limits=["300 per day", "60 per hour"])
