from django.contrib import admin
from django.urls import path, include
import django
import sys
from django.http import JsonResponse

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('core.urls')),
    
    # DEBUG — will be removed in production, just for checking versions
    path('debug-version-xyz/', lambda r: JsonResponse({
        'django': django.get_version(),
        'python': sys.version,
    })),
]