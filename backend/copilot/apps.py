from django.apps import AppConfig


class CopilotConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "copilot"

    def ready(self):
        # Signals disabled: enqueue is explicit in API (kb_upload_text)
        pass
