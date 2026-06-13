from django.urls import path
from . import api_views

urlpatterns = [
    path("tickets/", api_views.create_ticket, name="api_create_ticket"),
]
