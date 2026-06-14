from django.contrib import messages as django_messages
from django.contrib.auth import views as auth_views, update_session_auth_hash
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.tokens import default_token_generator
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
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
from .models import AuditLog, User, log_action


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
        return render(request, self.template_name, {
            "users": qs,
            "role_choices": User.Role.choices,
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
        })

    def post(self, request, pk):
        edit_user = self._get_user(pk)
        action = request.POST.get("action")

        if action == "edit":
            form = UserEditForm(request.POST, instance=edit_user)
            if form.is_valid():
                was_active = edit_user.is_active
                form.save()
                edit_user.refresh_from_db()
                action_type = (AuditLog.Action.USER_DEACTIVATE
                               if was_active and not edit_user.is_active
                               else AuditLog.Action.USER_UPDATE)
                log_action(request, action_type, target=edit_user.email)
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
