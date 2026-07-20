from django.conf import settings
from django.core.checks import Error, Tags, register


@register(Tags.security, deploy=True)
def legal_configuration_check(app_configs, **kwargs):
    if not settings.LEGAL_CONFIG_REQUIRED:
        return []
    missing = [name for name in ("OPERATOR_NAME", "OPERATOR_ADDRESS", "OPERATOR_EMAIL") if not getattr(settings, name)]
    return [Error("Public legal operator details are incomplete.", hint=f"Configure: {', '.join(missing)}", id="mycamino.E001")] if missing else []
