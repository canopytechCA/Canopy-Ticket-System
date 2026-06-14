from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="AuditLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("timestamp", models.DateTimeField(auto_now_add=True, db_index=True)),
                (
                    "actor",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="audit_logs",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "action",
                    models.CharField(
                        choices=[
                            ("LOGIN", "Login"),
                            ("LOGIN_FAILED", "Failed Login"),
                            ("LOGOUT", "Logout"),
                            ("TICKET_CREATE", "Ticket Created"),
                            ("TICKET_STATUS", "Ticket Status Changed"),
                            ("TICKET_ASSIGN", "Ticket Assigned"),
                            ("MESSAGE_ADD", "Message Added"),
                            ("ATTACHMENT_UPLOAD", "File Uploaded"),
                            ("TIME_LOG", "Time Logged"),
                            ("USER_CREATE", "User Created"),
                            ("USER_UPDATE", "User Updated"),
                            ("USER_DEACTIVATE", "User Deactivated"),
                            ("COMPANY_CREATE", "Company Created"),
                            ("COMPANY_UPDATE", "Company Updated"),
                            ("API_TICKET_CREATE", "Ticket Created via API"),
                        ],
                        db_index=True,
                        max_length=50,
                    ),
                ),
                ("target", models.CharField(blank=True, max_length=255)),
                ("detail", models.TextField(blank=True)),
                ("ip_address", models.GenericIPAddressField(blank=True, null=True)),
            ],
            options={
                "ordering": ["-timestamp"],
                "indexes": [
                    models.Index(fields=["actor", "-timestamp"], name="accounts_au_actor_i_idx"),
                ],
            },
        ),
    ]
