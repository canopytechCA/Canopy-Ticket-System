from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path("admin/", admin.site.urls),
    path("auth/", include("apps.accounts.urls")),
    path("portal/", include("apps.tickets.portal_urls")),
    path("api/", include("apps.tickets.api_urls")),
    path("", include("apps.tickets.urls")),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
