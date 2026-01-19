from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("copilot.api.urls")),
    path("ui/", include("ui.urls")),
]
