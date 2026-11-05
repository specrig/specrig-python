from django.urls import path

from . import views

urlpatterns = [
    path("items/<int:item>/", views.item),
    path("reservations/", views.Reservations.as_view()),
    path("files/<path:name>", views.file),
]
