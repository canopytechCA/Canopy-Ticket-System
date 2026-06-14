from django.urls import path
from . import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.LoginView.as_view(), name="login"),
    path("logout/", views.LogoutView.as_view(), name="logout"),
    path("profile/", views.ProfileView.as_view(), name="profile"),
    # Password reset flow
    path("password-reset/", views.CanopyPasswordResetView.as_view(), name="password_reset"),
    path("password-reset/done/", views.CanopyPasswordResetDoneView.as_view(), name="password_reset_done"),
    path("password-reset/confirm/<uidb64>/<token>/",
         views.CanopyPasswordResetConfirmView.as_view(), name="password_reset_confirm"),
    path("password-reset/complete/", views.CanopyPasswordResetCompleteView.as_view(), name="password_reset_complete"),
]
