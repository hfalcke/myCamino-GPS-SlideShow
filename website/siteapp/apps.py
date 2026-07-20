from django.apps import AppConfig


class SiteAppConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "siteapp"

    def ready(self):
        from . import checks  # noqa: F401
