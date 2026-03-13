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
SECRET_KEY   = os.environ.get('FLASK_SECRET_KEY', 'change-me-in-env')

# Server runtime settings
FLASK_ENV    = os.environ.get('FLASK_ENV', 'development')   # 'development' | 'production'
SERVER_HOST  = os.environ.get('SERVER_HOST', '0.0.0.0')
SERVER_PORT  = int(os.environ.get('SERVER_PORT', '5000'))

# SSL (required when FLASK_ENV=production)
SSL_CERT     = os.environ.get('SSL_CERT', '')   # path to fullchain.pem / cert.pem
SSL_KEY      = os.environ.get('SSL_KEY',  '')   # path to privkey.pem
