from django.shortcuts import redirect
from django.urls import path
from . import views
from apps.accounts import views as account_views

app_name = "tickets"


def root_redirect(request):
    if not request.user.is_authenticated:
        return redirect("accounts:login")
    if request.user.is_tech:
        return redirect("tickets:tech_dashboard")
    return redirect("portal:dashboard")


urlpatterns = [
    path("", root_redirect, name="root"),
    path("tech/", views.TechDashboard.as_view(), name="tech_dashboard"),
    path("tech/tickets/new/", views.TechTicketCreate.as_view(), name="tech_ticket_create"),
    path("tech/tickets/<int:pk>/", views.TechTicketDetail.as_view(), name="tech_ticket_detail"),
    path("tech/reports/", views.TechReports.as_view(), name="tech_reports"),
    path("tech/export/time/", views.TechTimeExport.as_view(), name="tech_time_export"),
    path("tech/companies/", views.TechCompanyList.as_view(), name="tech_company_list"),
    path("tech/companies/new/", views.TechCompanyCreate.as_view(), name="tech_company_create"),
    path("tech/companies/<int:pk>/", views.TechCompanyDetail.as_view(), name="tech_company_detail"),
    path("tech/bulk/", views.TechBulkAction.as_view(), name="tech_bulk_action"),
    path("tech/categories/", views.TechCategoryList.as_view(), name="tech_category_list"),
    path("tech/categories/<int:pk>/", views.TechCategoryDetail.as_view(), name="tech_category_detail"),
    path("tech/users/", account_views.TechUserList.as_view(), name="tech_user_list"),
    path("tech/users/new/", account_views.TechUserCreate.as_view(), name="tech_user_create"),
    path("tech/users/<int:pk>/", account_views.TechUserDetail.as_view(), name="tech_user_detail"),
    path("tech/audit/", account_views.TechAuditLog.as_view(), name="tech_audit_log"),
]
