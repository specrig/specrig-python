from django.urls import path

from reminders import views

urlpatterns = [
    path("reminders/<int:order>", views.last_reminder),
]
