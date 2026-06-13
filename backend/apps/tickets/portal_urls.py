from django.urls import path
from . import portal_views

app_name = "portal"

urlpatterns = [
    path("", portal_views.ClientDashboard.as_view(), name="dashboard"),
    path("new/", portal_views.ClientTicketCreate.as_view(), name="ticket_create"),
    path("<int:pk>/", portal_views.ClientTicketDetail.as_view(), name="ticket_detail"),
]
