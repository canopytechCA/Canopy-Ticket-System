from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse


def _protected_media(request, path):
    """Serve uploaded files only to authenticated users."""
    if not request.user.is_authenticated:
        from django.shortcuts import redirect
        return redirect(f"{settings.LOGIN_URL}?next={request.path}")
    if settings.DEBUG:
        from django.views.static import serve
        return serve(request, path, document_root=settings.MEDIA_ROOT)
    response = HttpResponse()
    response["X-Accel-Redirect"] = f"/internal-media/{path}"
    del response["Content-Type"]
    return response


admin_url = getattr(settings, "ADMIN_URL", "admin/")

urlpatterns = [
    path(admin_url, admin.site.urls),
    path("auth/", include("apps.accounts.urls")),
    path("portal/", include("apps.tickets.portal_urls")),
    path("api/", include("apps.tickets.api_urls")),
    path("", include("apps.tickets.urls")),
    path("media/<path:path>", _protected_media, name="protected_media"),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
