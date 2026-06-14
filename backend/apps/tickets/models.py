import os
import random

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from . import sla as sla_module

_TEN_MB = 10 * 1024 * 1024

_ALLOWED_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".csv", ".txt",
    ".png", ".jpg", ".jpeg", ".gif", ".webp",
    ".zip", ".7z", ".tar", ".gz",
    ".msg", ".eml",
    ".log",
}


def _validate_file_size(value):
    if value.size > _TEN_MB:
        raise ValidationError("File size must be 10 MB or less.")


def _validate_file_type(value):
    ext = os.path.splitext(value.name)[1].lower()
    if ext not in _ALLOWED_EXTENSIONS:
        raise ValidationError(
            f"File type '{ext}' is not allowed. "
            f"Allowed types: {', '.join(sorted(_ALLOWED_EXTENSIONS))}"
        )


class Category(models.Model):
    COLORS = [
        ("#3b82f6", "Blue"),
        ("#8b5cf6", "Purple"),
        ("#f97316", "Orange"),
        ("#ef4444", "Red"),
        ("#22c55e", "Green"),
        ("#eab308", "Yellow"),
        ("#6b7280", "Gray"),
        ("#ec4899", "Pink"),
    ]

    name = models.CharField(max_length=100, unique=True)
    color = models.CharField(max_length=7, default="#6b7280", choices=COLORS)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "categories"

    def __str__(self):
        return self.name


class Ticket(models.Model):
    class Status(models.TextChoices):
        OPEN = "OPEN", "Open"
        IN_PROGRESS = "IN_PROGRESS", "In Progress"
        WAITING_CLIENT = "WAITING_CLIENT", "Waiting on Client"
        RESOLVED = "RESOLVED", "Resolved"
        CLOSED = "CLOSED", "Closed"

    class Priority(models.TextChoices):
        LOW = "LOW", "Low"
        MEDIUM = "MEDIUM", "Medium"
        HIGH = "HIGH", "High"
        CRITICAL = "CRITICAL", "Critical"

    ticket_number = models.CharField(max_length=20, unique=True, editable=False)
    category = models.ForeignKey(
        Category,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tickets",
    )
    company = models.ForeignKey(
        "companies.Company", on_delete=models.PROTECT, related_name="tickets"
    )
    created_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        related_name="created_tickets",
    )
    assigned_to = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_tickets",
        limit_choices_to={"role": "TECH"},
    )
    subject = models.CharField(max_length=255)
    description = models.TextField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    priority = models.CharField(max_length=10, choices=Priority.choices, default=Priority.MEDIUM)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    # SLA
    sla_response_deadline = models.DateTimeField(null=True, blank=True)
    sla_resolve_deadline = models.DateTimeField(null=True, blank=True)
    first_response_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"[{self.ticket_number}] {self.subject}"

    def save(self, *args, **kwargs):
        is_new = not self.pk

        if not self.ticket_number:
            year = timezone.now().year
            for _ in range(10):
                candidate = f"T-{year}-{random.randint(10000, 99999)}"
                if not Ticket.objects.filter(ticket_number=candidate).exists():
                    self.ticket_number = candidate
                    break
            else:
                self.ticket_number = f"T-{year}-{random.randint(100000, 999999)}"

        if self.status == self.Status.RESOLVED and not self.resolved_at:
            self.resolved_at = timezone.now()
        elif self.status != self.Status.RESOLVED:
            self.resolved_at = None

        # Set SLA deadlines on creation
        if is_new and not self.sla_response_deadline:
            now = timezone.now()
            resp, res = sla_module.deadlines_for(self.priority, now)
            self.sla_response_deadline = resp
            self.sla_resolve_deadline = res

        super().save(*args, **kwargs)

    @property
    def is_open(self):
        return self.status not in (self.Status.RESOLVED, self.Status.CLOSED)

    @property
    def total_minutes(self):
        return self.time_entries.aggregate(
            total=models.Sum("minutes")
        )["total"] or 0

    @property
    def status_color(self):
        return {
            self.Status.OPEN: "blue",
            self.Status.IN_PROGRESS: "yellow",
            self.Status.WAITING_CLIENT: "purple",
            self.Status.RESOLVED: "green",
            self.Status.CLOSED: "gray",
        }.get(self.status, "gray")

    @property
    def priority_color(self):
        return {
            self.Priority.LOW: "gray",
            self.Priority.MEDIUM: "blue",
            self.Priority.HIGH: "orange",
            self.Priority.CRITICAL: "red",
        }.get(self.priority, "gray")

    @property
    def sla_response_status(self):
        return sla_module.response_status(self)

    @property
    def sla_resolve_status(self):
        return sla_module.resolve_status(self)

    @property
    def sla_resolve_display(self):
        if not self.sla_resolve_deadline:
            return ""
        return sla_module.time_remaining_display(self.sla_resolve_deadline)


class Message(models.Model):
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="messages")
    author = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, null=True)
    body = models.TextField()
    is_internal = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"Message on {self.ticket.ticket_number} by {self.author}"


class TimeEntry(models.Model):
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="time_entries")
    tech = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        related_name="time_entries",
        limit_choices_to={"role": "TECH"},
    )
    minutes = models.PositiveIntegerField()
    description = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "Time entries"

    def __str__(self):
        return f"{self.minutes}min on {self.ticket.ticket_number} by {self.tech}"

    @property
    def hours_display(self):
        h, m = divmod(self.minutes, 60)
        return f"{h}h {m}m" if h else f"{m}m"


class Attachment(models.Model):
    message = models.ForeignKey(Message, on_delete=models.CASCADE, related_name="attachments")
    file = models.FileField(upload_to="attachments/%Y/%m/", validators=[_validate_file_size, _validate_file_type])
    filename = models.CharField(max_length=255)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["uploaded_at"]

    def __str__(self):
        return self.filename

    @property
    def extension(self):
        return os.path.splitext(self.filename)[1].lower()

    @property
    def is_image(self):
        return self.extension in (".jpg", ".jpeg", ".png", ".gif", ".webp")
