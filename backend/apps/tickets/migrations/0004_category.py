from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("tickets", "0003_alter_attachment_file"),
    ]

    operations = [
        migrations.CreateModel(
            name="Category",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=100, unique=True)),
                (
                    "color",
                    models.CharField(
                        choices=[
                            ("#3b82f6", "Blue"),
                            ("#8b5cf6", "Purple"),
                            ("#f97316", "Orange"),
                            ("#ef4444", "Red"),
                            ("#22c55e", "Green"),
                            ("#eab308", "Yellow"),
                            ("#6b7280", "Gray"),
                            ("#ec4899", "Pink"),
                        ],
                        default="#6b7280",
                        max_length=7,
                    ),
                ),
                ("is_active", models.BooleanField(default=True)),
            ],
            options={
                "verbose_name_plural": "categories",
                "ordering": ["name"],
            },
        ),
        migrations.AddField(
            model_name="ticket",
            name="category",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="tickets",
                to="tickets.category",
            ),
        ),
    ]
