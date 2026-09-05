from django.urls import path
from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("api/app-news/v1/", views.application_news, name="application-news"),
    path("documentation/", views.documentation_index, name="documentation"),
    path("documentation/<slug:guide>/", views.documentation_detail, name="documentation-detail"),
    path("faq/", views.faq, name="faq"),
    path("contact/", views.contact, name="contact"),
    path("privacy/", views.privacy, name="privacy"),
    path("imprint/", views.imprint, name="imprint"),
    path("download/", views.beta_download, name="beta-download"),
    path("beta/verify/<str:token>/", views.verify_beta, name="verify-beta"),
    path("downloads/myCamino-GPS-Track-Show.dmg", views.protected_download_fallback, name="protected-download"),
    path("internal/authorize-download/", views.authorize_download, name="authorize-download"),
    path("health/", views.health, name="health"),
]
