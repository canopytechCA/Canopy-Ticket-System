import logging
from datetime import timedelta

from django.contrib import messages as django_messages
from django.contrib.auth import login as auth_login
from django.contrib.auth import views as auth_views, update_session_auth_hash

logger = logging.getLogger(__name__)
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.tokens import default_token_generator
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from django.views.generic import ListView, View
from django_ratelimit.decorators import ratelimit

from apps.tickets.mixins import TechRequiredMixin
from .forms import (
    AdminPasswordForm, LoginForm, ProfileForm,
    SelfPasswordChangeForm, UserCreateForm, UserEditForm,
)
from .models import ARCHIVE_RETENTION_DAYS, SUPPORT_PHONE, AuditLog, User, log_action


class LoginView(auth_views.LoginView):
    form_class = LoginForm
    template_name = "accounts/login.html"

    @method_decorator(ratelimit(key="ip", rate="5/m", block=False))
    def post(self, request, *args, **kwargs):
        if getattr(request, "limited", False):
            form = self.get_form()
            form.add_error(None, "Too many login attempts. Please wait a minute and try again.")
            return self.form_invalid(form)
        return super().post(request, *args, **kwargs)

    def get_success_url(self):
        user = self.request.user
        if user.is_tech:
            return "/tech/"
        return "/portal/"


class LogoutView(auth_views.LogoutView):
    pass


# ── User management (tech portal) ────────────────────────────────────────────

class TechUserList(TechRequiredMixin, View):
    template_name = "tech/user_list.html"

    def get(self, request):
        qs = User.objects.select_related("company").order_by("last_name", "first_name")
        role = request.GET.get("role")
        if role:
            qs = qs.filter(role=role)
        status = request.GET.get("status")
        if status:
            qs = qs.filter(status=status)
        else:
            qs = qs.exclude(status=User.Status.ARCHIVED)
        return render(request, self.template_name, {
            "users": qs,
            "role_choices": User.Role.choices,
            "status_choices": User.Status.choices,
            "filters": request.GET,
        })


class TechUserCreate(TechRequiredMixin, View):
    template_name = "tech/user_create.html"

    def get(self, request):
        return render(request, self.template_name, {"form": UserCreateForm()})

    def post(self, request):
        form = UserCreateForm(request.POST)
        if form.is_valid():
            user = form.save()
            log_action(request, AuditLog.Action.USER_CREATE, target=user.email,
                       detail=f"role={user.role} company={user.company}")
            django_messages.success(request, f"{user.get_full_name()} created.")
            return redirect("tickets:tech_user_detail", pk=user.pk)
        return render(request, self.template_name, {"form": form})


class TechUserDetail(TechRequiredMixin, View):
    template_name = "tech/user_detail.html"

    def _get_user(self, pk):
        return get_object_or_404(User.objects.select_related("company"), pk=pk)

    def get(self, request, pk):
        edit_user = self._get_user(pk)
        return render(request, self.template_name, {
            "edit_user": edit_user,
            "form": UserEditForm(instance=edit_user),
            "password_form": AdminPasswordForm(),
            "support_phone": SUPPORT_PHONE,
        })

    def post(self, request, pk):
        edit_user = self._get_user(pk)
        action = request.POST.get("action")

        if action == "edit":
            form = UserEditForm(request.POST, instance=edit_user)
            if form.is_valid():
                form.save()
                log_action(request, AuditLog.Action.USER_UPDATE, target=edit_user.email)
                django_messages.success(request, "User updated.")
                return redirect("tickets:tech_user_detail", pk=pk)
            return render(request, self.template_name, {
                "edit_user": edit_user,
                "form": form,
                "password_form": AdminPasswordForm(),
            })

        if action == "password":
            password_form = AdminPasswordForm(request.POST)
            if password_form.is_valid():
                edit_user.set_password(password_form.cleaned_data["password"])
                edit_user.save(update_fields=["password"])
                log_action(request, AuditLog.Action.USER_UPDATE, target=edit_user.email,
                           detail="password reset by admin")
                django_messages.success(request, "Password updated.")
                return redirect("tickets:tech_user_detail", pk=pk)
            return render(request, self.template_name, {
                "edit_user": edit_user,
                "form": UserEditForm(instance=edit_user),
                "password_form": password_form,
            })

        if action in ("block", "unblock", "restore"):
            if not request.user.is_superuser:
                raise PermissionDenied
            if edit_user == request.user:
                django_messages.error(request, "You can't block or archive your own account.")
                return redirect("tickets:tech_user_detail", pk=pk)

            if action == "block":
                edit_user.block()
                log_action(request, AuditLog.Action.USER_BLOCK, target=edit_user.email)
                django_messages.success(
                    request, f"{edit_user.get_full_name()} is now blocked from signing in."
                )
            elif action == "unblock":
                edit_user.unblock()
                log_action(request, AuditLog.Action.USER_UNBLOCK, target=edit_user.email)
                django_messages.success(request, f"{edit_user.get_full_name()} can sign in again.")
            elif action == "restore":
                edit_user.restore()
                log_action(request, AuditLog.Action.USER_RESTORE, target=edit_user.email)
                django_messages.success(request, f"{edit_user.get_full_name()} restored to active.")
            return redirect("tickets:tech_user_detail", pk=pk)

        return redirect("tickets:tech_user_detail", pk=pk)


class TechUserArchive(TechRequiredMixin, View):
    """Archive a user: super-user only, confirmed via a dedicated page since
    it starts the clock on a permanent, irreversible deletion."""
    template_name = "tech/user_archive_confirm.html"

    def _get_user(self, pk):
        return get_object_or_404(User.objects.select_related("company"), pk=pk)

    def get(self, request, pk):
        if not request.user.is_superuser:
            raise PermissionDenied
        edit_user = self._get_user(pk)
        purge_date = timezone.now() + timedelta(days=ARCHIVE_RETENTION_DAYS)
        return render(request, self.template_name, {"edit_user": edit_user, "purge_date": purge_date})

    def post(self, request, pk):
        if not request.user.is_superuser:
            raise PermissionDenied
        edit_user = self._get_user(pk)
        if edit_user == request.user:
            django_messages.error(request, "You can't block or archive your own account.")
            return redirect("tickets:tech_user_detail", pk=pk)

        edit_user.archive()
        log_action(
            request, AuditLog.Action.USER_ARCHIVE, target=edit_user.email,
            detail=f"scheduled purge {edit_user.purge_at:%Y-%m-%d}",
        )
        django_messages.success(
            request,
            f"{edit_user.get_full_name()} archived. Their data will be permanently removed on "
            f"{edit_user.purge_at.strftime('%B %d, %Y')}.",
        )
        return redirect("tickets:tech_user_detail", pk=pk)


class TechAuditLog(TechRequiredMixin, ListView):
    template_name = "tech/audit_log.html"
    context_object_name = "entries"
    paginate_by = 50

    def get_queryset(self):
        qs = AuditLog.objects.select_related("actor").order_by("-timestamp")
        action = self.request.GET.get("action")
        if action:
            qs = qs.filter(action=action)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["action_choices"] = AuditLog.Action.choices
        ctx["filters"] = self.request.GET
        params = self.request.GET.copy()
        params.pop("page", None)
        ctx["filter_params"] = params.urlencode()
        return ctx


# ── Password reset (uses Graph API email, not Django email backend) ───────────

class CanopyPasswordResetView(auth_views.PasswordResetView):
    template_name = "accounts/password_reset.html"
    email_template_name = None  # we handle email ourselves

    def form_valid(self, form):
        from apps.accounts.email_service import send_email
        from django.conf import settings

        for user in form.get_users(form.cleaned_data["email"]):
            uid = urlsafe_base64_encode(force_bytes(user.pk))
            token = default_token_generator.make_token(user)
            reset_url = self.request.build_absolute_uri(
                f"/auth/password-reset/confirm/{uid}/{token}/"
            )
            html = render_to_string("email/password_reset.html", {
                "user": user,
                "recipient_name": user.get_full_name(),
                "reset_url": reset_url,
            })
            send_email(
                user.email,
                user.get_full_name(),
                "Reset your Canopy Tickets password",
                html,
            )

        from django.http import HttpResponseRedirect
        return HttpResponseRedirect(self.get_success_url())

    def get_success_url(self):
        return "/auth/password-reset/done/"


class CanopyPasswordResetDoneView(auth_views.PasswordResetDoneView):
    template_name = "accounts/password_reset_done.html"


class CanopyPasswordResetConfirmView(auth_views.PasswordResetConfirmView):
    template_name = "accounts/password_reset_confirm.html"

    def get_success_url(self):
        return "/auth/password-reset/complete/"


class CanopyPasswordResetCompleteView(auth_views.PasswordResetCompleteView):
    template_name = "accounts/password_reset_complete.html"


# ── User profile (self-service name + password) ───────────────────────────────

class ProfileView(LoginRequiredMixin, View):

    def _template(self, request):
        return "tech/profile.html" if request.user.is_tech else "portal/profile.html"

    def get(self, request):
        return render(request, self._template(request), {
            "profile_form": ProfileForm(instance=request.user),
            "password_form": SelfPasswordChangeForm(user=request.user),
        })

    def post(self, request):
        action = request.POST.get("action")

        if action == "profile":
            form = ProfileForm(request.POST, instance=request.user)
            if form.is_valid():
                form.save()
                log_action(request, AuditLog.Action.USER_UPDATE,
                           target=request.user.email, detail="self profile update")
                django_messages.success(request, "Profile updated.")
                return redirect("accounts:profile")
            return render(request, self._template(request), {
                "profile_form": form,
                "password_form": SelfPasswordChangeForm(user=request.user),
            })

        if action == "password":
            form = SelfPasswordChangeForm(user=request.user, data=request.POST)
            if form.is_valid():
                request.user.set_password(form.cleaned_data["new_password"])
                request.user.save(update_fields=["password"])
                update_session_auth_hash(request, request.user)  # keep logged in
                log_action(request, AuditLog.Action.USER_UPDATE,
                           target=request.user.email, detail="self password change")
                django_messages.success(request, "Password updated.")
                return redirect("accounts:profile")
            return render(request, self._template(request), {
                "profile_form": ProfileForm(instance=request.user),
                "password_form": form,
            })

        return redirect("accounts:profile")


# ── Microsoft 365 SSO ─────────────────────────────────────────────────────────

def _auto_create_from_domain(request, email: str, info: dict):
    """
    Look up a Company by the email domain and auto-create a CLIENT user.
    Returns the new User on success, or None (with a flash error) on failure.
    """
    from apps.companies.models import Company

    domain = email.split("@")[1] if "@" in email else ""
    if not domain:
        django_messages.error(request, f"No active account found for {email}. Contact your administrator.")
        return None

    try:
        company = Company.objects.get(email_domain__iexact=domain, is_active=True)
    except Company.DoesNotExist:
        django_messages.error(
            request,
            f"No active account found for {email}. Contact your administrator to get access.",
        )
        return None

    # Parse name — fall back to splitting display_name if Graph didn't return separate fields
    first_name = info.get("first_name", "").strip()
    last_name = info.get("last_name", "").strip()
    if not first_name and not last_name:
        parts = info.get("display_name", "").strip().split(" ", 1)
        first_name = parts[0] if parts else ""
        last_name = parts[1] if len(parts) > 1 else ""

    user = User.objects.create_user(
        email=email,
        password=None,          # set_unusable_password — SSO only
        first_name=first_name,
        last_name=last_name or first_name,
        role=User.Role.CLIENT,
        company=company,
    )

    log_action(
        request, AuditLog.Action.USER_CREATE, target=email,
        detail=f"auto-created via Microsoft SSO for {company.name}",
    )
    logger.info("Auto-created client account for %s (company: %s)", email, company.name)
    return user


class MicrosoftLoginView(View):
    """Redirect the user to Microsoft's OAuth2 authorization endpoint."""

    def get(self, request):
        from .microsoft_auth import get_authorize_url
        from django.conf import settings as djsettings
        if not getattr(djsettings, "AZURE_CLIENT_ID", ""):
            django_messages.error(request, "Microsoft sign-in is not configured.")
            return redirect("accounts:login")
        return redirect(get_authorize_url(request))


class MicrosoftCallbackView(View):
    """Handle the OAuth2 callback from Microsoft, log in the matching user."""

    def get(self, request):
        from .microsoft_auth import verify_state, get_user_info

        if request.GET.get("error"):
            django_messages.error(request, "Microsoft sign-in was cancelled.")
            return redirect("accounts:login")

        if not verify_state(request, request.GET.get("state", "")):
            django_messages.error(request, "Invalid authentication state. Please try again.")
            return redirect("accounts:login")

        code = request.GET.get("code", "")
        if not code:
            django_messages.error(request, "No authorization code received from Microsoft.")
            return redirect("accounts:login")

        try:
            info = get_user_info(request, code)
        except RuntimeError as e:
            logger.error("Microsoft SSO error: %s", e)
            django_messages.error(request, "Could not complete Microsoft sign-in. Please try again.")
            return redirect("accounts:login")

        email = info["email"]

        try:
            existing_user = User.objects.get(email__iexact=email)
        except User.DoesNotExist:
            existing_user = None

        if existing_user is not None:
            if existing_user.status == User.Status.BLOCKED:
                django_messages.error(
                    request,
                    f"This account has been blocked. Please call our support line at {SUPPORT_PHONE}.",
                )
                return redirect("accounts:login")
            if not existing_user.is_active:
                django_messages.error(
                    request, f"No active account found for {email}. Contact your administrator."
                )
                return redirect("accounts:login")
            user = existing_user
        else:
            user = _auto_create_from_domain(request, email, info)
            if user is None:
                return redirect("accounts:login")

        auth_login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        log_action(request, AuditLog.Action.LOGIN, target=user.email, detail="Microsoft SSO")

        if user.is_tech:
            return redirect("/tech/")
        return redirect("/portal/")
