from django.contrib.auth import views as auth_views
from django.shortcuts import redirect
from django.utils.decorators import method_decorator
from django_ratelimit.decorators import ratelimit
from .forms import LoginForm


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
