from django.apps import AppConfig


class CopilotConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "copilot"

    def ready(self):
        # noqa: F401
        import copilot.signals
