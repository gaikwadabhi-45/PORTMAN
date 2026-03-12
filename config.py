import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.environ.get('DATABASE_URL', 'postgresql://postgres:password@localhost:5432/portman')
SECRET_KEY = os.environ.get('FLASK_SECRET_KEY', 'change-me-in-env')
