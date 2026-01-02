from django.urls import path
from .views import (
    health,
    kb_upload_text, kb_documents, kb_document_detail,
    ask,
    runs_list, run_detail, run_steps,
)

urlpatterns = [
    path("health/", health),

    path("kb/upload_text/", kb_upload_text),
    path("kb/documents/", kb_documents),
    path("kb/documents/<int:document_id>/", kb_document_detail),

    path("ask/", ask),

    # Traces
    path("runs/", runs_list),
    path("runs/<int:run_id>/", run_detail),
    path("runs/<int:run_id>/steps/", run_steps),
]
