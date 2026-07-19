from datetime import timedelta

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone

SUPPORT_PHONE = "647-478-8449"
ARCHIVE_RETENTION_DAYS = 365


class UserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("Email is required")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("role", User.Role.TECH)
        return self.create_user(email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    class Role(models.TextChoices):
        TECH = "TECH", "Technician"
        CLIENT = "CLIENT", "Client"

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        BLOCKED = "BLOCKED", "Blocked"
        ARCHIVED = "ARCHIVED", "Archived"

    email = models.EmailField(unique=True)
    first_name = models.CharField(max_length=150)
    last_name = models.CharField(max_length=150)
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.CLIENT)
    company = models.ForeignKey(
        "companies.Company",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="users",
    )
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(auto_now_add=True)

    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    blocked_at = models.DateTimeField(null=True, blank=True)
    archived_at = models.DateTimeField(null=True, blank=True)
    purge_at = models.DateTimeField(
        null=True, blank=True,
        help_text="When an archived user's account is permanently deleted.",
    )

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["first_name", "last_name"]

    class Meta:
        verbose_name = "User"
        verbose_name_plural = "Users"
        ordering = ["last_name", "first_name"]

    def __str__(self):
        return f"{self.get_full_name()} ({self.email})"

    def get_full_name(self):
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def is_tech(self):
        return self.role == self.Role.TECH

    @property
    def is_client(self):
        return self.role == self.Role.CLIENT

    @property
    def is_blocked(self):
        return self.status == self.Status.BLOCKED

    @property
    def is_archived(self):
        return self.status == self.Status.ARCHIVED

    def block(self):
        """Prevent sign-in. Reversible via unblock()."""
        self.status = self.Status.BLOCKED
        self.is_active = False
        self.blocked_at = timezone.now()
        self.archived_at = None
        self.purge_at = None
        self.save(update_fields=["status", "is_active", "blocked_at", "archived_at", "purge_at"])

    def unblock(self):
        self.status = self.Status.ACTIVE
        self.is_active = True
        self.blocked_at = None
        self.save(update_fields=["status", "is_active", "blocked_at"])

    def archive(self):
        """Soft-remove the user. Their data stays put; the account itself is
        hard-deleted once purge_at passes (see purge_archived_users command)."""
        now = timezone.now()
        self.status = self.Status.ARCHIVED
        self.is_active = False
        self.archived_at = now
        self.purge_at = now + timedelta(days=ARCHIVE_RETENTION_DAYS)
        self.blocked_at = None
        self.save(update_fields=["status", "is_active", "archived_at", "purge_at", "blocked_at"])

    def restore(self):
        self.status = self.Status.ACTIVE
        self.is_active = True
        self.archived_at = None
        self.purge_at = None
        self.save(update_fields=["status", "is_active", "archived_at", "purge_at"])


class AuditLog(models.Model):
    class Action(models.TextChoices):
        LOGIN = "LOGIN", "Login"
        LOGIN_FAILED = "LOGIN_FAILED", "Failed Login"
        LOGOUT = "LOGOUT", "Logout"
        TICKET_CREATE = "TICKET_CREATE", "Ticket Created"
        TICKET_STATUS = "TICKET_STATUS", "Ticket Status Changed"
        TICKET_ASSIGN = "TICKET_ASSIGN", "Ticket Assigned"
        MESSAGE_ADD = "MESSAGE_ADD", "Message Added"
        ATTACHMENT_UPLOAD = "ATTACHMENT_UPLOAD", "File Uploaded"
        TIME_LOG = "TIME_LOG", "Time Logged"
        USER_CREATE = "USER_CREATE", "User Created"
        USER_UPDATE = "USER_UPDATE", "User Updated"
        USER_DEACTIVATE = "USER_DEACTIVATE", "User Deactivated"
        USER_BLOCK = "USER_BLOCK", "User Blocked"
        USER_UNBLOCK = "USER_UNBLOCK", "User Unblocked"
        USER_ARCHIVE = "USER_ARCHIVE", "User Archived"
        USER_RESTORE = "USER_RESTORE", "User Restored"
        USER_PURGE = "USER_PURGE", "User Permanently Deleted"
        COMPANY_CREATE = "COMPANY_CREATE", "Company Created"
        COMPANY_UPDATE = "COMPANY_UPDATE", "Company Updated"
        API_TICKET_CREATE = "API_TICKET_CREATE", "Ticket Created via API"
        TICKET_DELETE = "TICKET_DELETE", "Ticket Deleted"
        TICKET_MERGE = "TICKET_MERGE", "Tickets Merged"
        EMAIL_TICKET_CREATE = "EMAIL_TICKET_CREATE", "Ticket Created via Email"

    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    actor = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_logs",
    )
    action = models.CharField(max_length=50, choices=Action.choices, db_index=True)
    target = models.CharField(max_length=255, blank=True)
    detail = models.TextField(blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ["-timestamp"]
        indexes = [
            models.Index(fields=["actor", "-timestamp"]),
        ]

    def __str__(self):
        return f"{self.timestamp:%Y-%m-%d %H:%M} | {self.action} | {self.actor}"


def log_action(request, action, target="", detail=""):
    """Record a security/audit event. Safe to call from any view or signal."""
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    ip = forwarded.split(",")[0].strip() if forwarded else request.META.get("REMOTE_ADDR")
    user = getattr(request, "user", None)
    actor = user if (user and getattr(user, "is_authenticated", False)) else None
    AuditLog.objects.create(
        actor=actor,
        action=action,
        target=target,
        detail=detail,
        ip_address=ip or None,
    )
