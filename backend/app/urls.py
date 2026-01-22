from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.views.generic import RedirectView

urlpatterns = [
    path("", RedirectView.as_view(url="/ui/", permanent=False), name="thin-ui"),
    path("admin/", admin.site.urls),
    path("api/", include("copilot.api.urls")),
    path("ui/", include("ui.urls")),
]


# dev-only static
if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
