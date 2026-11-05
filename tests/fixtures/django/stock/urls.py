from django.urls import include, path, re_path

from . import views

urlpatterns = [
    path("api/", include("stock.api")),
    re_path(r"^legacy/(?P<sku>[a-z]+)/$", views.legacy),
]
