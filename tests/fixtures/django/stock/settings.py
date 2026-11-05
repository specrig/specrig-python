import os

import environ

env = environ.Env()

SECRET_KEY = env("SECRET_KEY")
DEBUG = env.bool("DEBUG", default=False)
WAREHOUSE_URL = env.str("WAREHOUSE_URL")
RESERVE_LIMIT = int(os.environ.get("RESERVE_LIMIT", "5"))

INSTALLED_APPS = ["stock"]
ROOT_URLCONF = "stock.urls"
DATABASES = {"default": env.db("DATABASE_URL")}
