from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("manage/", admin.site.urls),
    path("", include("siteapp.urls")),
]
