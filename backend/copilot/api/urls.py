from django.urls import path
from .views import (
    api_index,
    health,
    kb_upload_text, kb_documents, kb_document_detail,
    ask,
    runs_list, run_detail, run_steps,    kb_upload_file,

)

urlpatterns = [
    path("kb/upload_file/", kb_upload_file),
    path("", api_index),
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
