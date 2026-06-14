from django import forms
from apps.companies.models import Company
from .models import Category, Ticket, Message, TimeEntry


INPUT_CLASS = "w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent text-sm"
SELECT_CLASS = "w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 bg-white text-sm"
TEXTAREA_CLASS = "w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent text-sm resize-y"


class TicketForm(forms.ModelForm):
    class Meta:
        model = Ticket
        fields = ["company", "category", "subject", "description", "priority", "assigned_to"]
        widgets = {
            "company": forms.Select(attrs={"class": SELECT_CLASS}),
            "category": forms.Select(attrs={"class": SELECT_CLASS}),
            "subject": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "Brief summary of the issue"}),
            "description": forms.Textarea(attrs={"class": TEXTAREA_CLASS, "rows": 6, "placeholder": "Describe the issue in detail..."}),
            "priority": forms.Select(attrs={"class": SELECT_CLASS}),
            "assigned_to": forms.Select(attrs={"class": SELECT_CLASS}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.accounts.models import User
        self.fields["assigned_to"].queryset = User.objects.filter(role=User.Role.TECH, is_active=True)
        self.fields["assigned_to"].required = False
        self.fields["assigned_to"].empty_label = "— Unassigned —"
        self.fields["category"].queryset = Category.objects.filter(is_active=True)
        self.fields["category"].required = False
        self.fields["category"].empty_label = "— No category —"


class ClientTicketForm(forms.ModelForm):
    """Simplified form for client portal — no company/assignment fields."""
    class Meta:
        model = Ticket
        fields = ["subject", "description", "priority"]
        widgets = {
            "subject": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "Brief summary of the issue"}),
            "description": forms.Textarea(attrs={"class": TEXTAREA_CLASS, "rows": 6, "placeholder": "Describe the issue in detail..."}),
            "priority": forms.Select(attrs={"class": SELECT_CLASS}),
        }


class MessageForm(forms.ModelForm):
    class Meta:
        model = Message
        fields = ["body", "is_internal"]
        widgets = {
            "body": forms.Textarea(attrs={"class": TEXTAREA_CLASS, "rows": 4, "placeholder": "Write your reply..."}),
        }

    def __init__(self, *args, is_tech=False, **kwargs):
        super().__init__(*args, **kwargs)
        if not is_tech:
            self.fields.pop("is_internal")


class TimeEntryForm(forms.ModelForm):
    class Meta:
        model = TimeEntry
        fields = ["minutes", "description"]
        widgets = {
            "minutes": forms.NumberInput(attrs={"class": INPUT_CLASS, "min": 1, "placeholder": "e.g. 30"}),
            "description": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "What did you work on?"}),
        }

    def clean_minutes(self):
        value = self.cleaned_data["minutes"]
        if value < 1:
            raise forms.ValidationError("Must be at least 1 minute.")
        return value


class TicketStatusForm(forms.ModelForm):
    class Meta:
        model = Ticket
        fields = ["status", "priority", "category", "assigned_to"]
        widgets = {
            "status": forms.Select(attrs={"class": SELECT_CLASS}),
            "priority": forms.Select(attrs={"class": SELECT_CLASS}),
            "category": forms.Select(attrs={"class": SELECT_CLASS}),
            "assigned_to": forms.Select(attrs={"class": SELECT_CLASS}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.accounts.models import User
        self.fields["assigned_to"].queryset = User.objects.filter(role=User.Role.TECH, is_active=True)
        self.fields["assigned_to"].required = False
        self.fields["assigned_to"].empty_label = "— Unassigned —"
        self.fields["category"].queryset = Category.objects.filter(is_active=True)
        self.fields["category"].required = False
        self.fields["category"].empty_label = "— No category —"


class CategoryForm(forms.ModelForm):
    class Meta:
        model = Category
        fields = ["name", "color", "is_active"]
        widgets = {
            "name": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "e.g. Network & Connectivity"}),
            "color": forms.Select(attrs={"class": SELECT_CLASS}),
        }


class CompanyForm(forms.ModelForm):
    class Meta:
        model = Company
        fields = ["name", "email_domain", "phone", "website", "notes", "is_active"]
        widgets = {
            "name": forms.TextInput(attrs={"class": INPUT_CLASS}),
            "email_domain": forms.TextInput(attrs={
                "class": INPUT_CLASS,
                "placeholder": "acme.com",
            }),
            "phone": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "+1 (780) 555-0100"}),
            "website": forms.URLInput(attrs={"class": INPUT_CLASS, "placeholder": "https://"}),
            "notes": forms.Textarea(attrs={"class": TEXTAREA_CLASS, "rows": 3}),
        }

    def clean_email_domain(self):
        domain = (self.cleaned_data.get("email_domain") or "").strip().lower()
        if domain.startswith("@"):
            domain = domain[1:]
        return domain or None
