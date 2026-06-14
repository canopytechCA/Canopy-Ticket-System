from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.accounts"
    label = "accounts"

    def ready(self):
        from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
        from .models import AuditLog, log_action

        def on_login(sender, request, user, **kwargs):
            log_action(request, AuditLog.Action.LOGIN, target=user.email)

        def on_logout(sender, request, user, **kwargs):
            if user:
                log_action(request, AuditLog.Action.LOGOUT, target=user.email)

        def on_login_failed(sender, credentials, request, **kwargs):
            log_action(
                request,
                AuditLog.Action.LOGIN_FAILED,
                target=credentials.get("username", ""),
            )

        user_logged_in.connect(on_login)
        user_logged_out.connect(on_logout)
        user_login_failed.connect(on_login_failed)
