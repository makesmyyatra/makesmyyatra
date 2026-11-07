import os
from pathlib import Path
import dj_database_url
from decouple import config, Csv
from django.core.exceptions import ImproperlyConfigured

RATELIMIT_VIEW = 'core.views.ratelimited_error'
RATELIMIT_FAIL_OPEN = False 
BASE_DIR = Path(__file__).resolve().parent.parent
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'makesmyyatra-ratelimit',
    }
}

# Logging Configuration
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '[{levelname}] {asctime} {name}: {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'INFO',
    },
    'loggers': {
        'django': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
        'django.request': {
            'handlers': ['console'],
            'level': 'ERROR',
            'propagate': False,
        },
        'core': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
    },
}
# Security Settings
DEBUG = config('DEBUG', default=False, cast=bool)

SECRET_KEY = config('SECRET_KEY', default='')
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = 'django-insecure-dev-only-not-for-production'
    else:
        raise ImproperlyConfigured(
            'SECRET_KEY environment variable is required in production. '
            'Set it in Render Dashboard → Environment → SECRET_KEY.'
        )

LOGIN_URL = 'partner_login'

# Allowed Hosts & CSRF for Render / Custom Domain
ALLOWED_HOSTS = config(
    'ALLOWED_HOSTS',
    default='localhost,127.0.0.1,makesmyyatra-1.onrender.com,makesmyyatra.com,www.makesmyyatra.com',
    cast=Csv()
)

CSRF_TRUSTED_ORIGINS = [
    'https://makesmyyatra-1.onrender.com',
    'https://makesmyyatra.com',
    'https://www.makesmyyatra.com',
]

# Application definition
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'core',  
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]
    
ROOT_URLCONF = 'makemyyatra.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'makemyyatra.wsgi.application'

DATABASE_URL = config('DATABASE_URL', default='')
if not DATABASE_URL:
    if DEBUG:
        DATABASE_URL = 'sqlite:///' + str(BASE_DIR / 'db.sqlite3')
    else:
        raise ImproperlyConfigured(
            'DATABASE_URL environment variable is required in production.'
        )

DATABASES = {
    'default': dj_database_url.config(
        default=DATABASE_URL,
        conn_max_age=600,
        conn_health_checks=True,
        ssl_require=not DEBUG
    )
}

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# Internationalization
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Asia/Kolkata'
USE_I18N = True
USE_TZ = True

# Static files (CSS, JavaScript, Images)
STATIC_URL = '/static/'
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedStaticFilesStorage",
    },
}

# Media Files
MEDIA_URL = '/media/'
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')

if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31536000   # 1 year
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_REFERRER_POLICY = 'same-origin'
    X_FRAME_OPTIONS = 'DENY'

# Default primary key field type
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
CACHE_CONTROL = 'no-cache, no-store, must-revalidate'

# Email Settings
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = config('EMAIL_HOST', default='smtp.office365.com')
EMAIL_PORT = config('EMAIL_PORT', default=587, cast=int)
EMAIL_USE_TLS = config('EMAIL_USE_TLS', default=True, cast=bool)
EMAIL_HOST_USER = config('EMAIL_HOST_USER', default='')
EMAIL_HOST_PASSWORD = config('EMAIL_HOST_PASSWORD', default='')
DEFAULT_FROM_EMAIL = config('DEFAULT_FROM_EMAIL', default='MakesMyYatra <bookings@makesmyyatra.com>')

# Third-party APIs
FAST2SMS_API_KEY = config('FAST2SMS_API_KEY', default='')
# Admin URL — security through obscurity
ADMIN_URL = os.environ.get('ADMIN_URL', 'admin/')
