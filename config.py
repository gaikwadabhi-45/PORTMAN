import os
from urllib.parse import urlparse, quote, urlunparse
from dotenv import load_dotenv

load_dotenv()


def _safe_db_url(url):
    """Re-encode the password in a DATABASE_URL to handle special characters like %, ], @."""
    try:
        p = urlparse(url)
        if p.password:
            encoded = quote(p.password, safe='')
            netloc = f"{p.username}:{encoded}@{p.hostname}"
            if p.port:
                netloc += f":{p.port}"
            return urlunparse(p._replace(netloc=netloc))
    except Exception:
        pass
    return url


DATABASE_URL = _safe_db_url(os.environ.get('DATABASE_URL', 'postgresql://postgres:postgres@localhost:5432/portman'))
SECRET_KEY = os.environ.get('FLASK_SECRET_KEY', 'change-me-in-env')
