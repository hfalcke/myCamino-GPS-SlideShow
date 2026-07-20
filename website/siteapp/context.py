from django.conf import settings


def site_settings(request):
    return {"operator_email": settings.OPERATOR_EMAIL}
