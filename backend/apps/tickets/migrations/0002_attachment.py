from django.db import migrations, models
import django.db.models.deletion
import apps.tickets.models


class Migration(migrations.Migration):

    dependencies = [
        ("tickets", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="Attachment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("file", models.FileField(upload_to="attachments/%Y/%m/", validators=[apps.tickets.models._validate_file_size])),
                ("filename", models.CharField(max_length=255)),
                ("uploaded_at", models.DateTimeField(auto_now_add=True)),
                (
                    "message",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="attachments",
                        to="tickets.message",
                    ),
                ),
            ],
            options={
                "ordering": ["uploaded_at"],
            },
        ),
    ]
