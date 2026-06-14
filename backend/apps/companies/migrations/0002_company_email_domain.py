from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("companies", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="company",
            name="email_domain",
            field=models.CharField(
                blank=True,
                help_text="e.g. acme.com — users signing in via Microsoft with this domain get a client account auto-created",
                max_length=100,
                null=True,
                unique=True,
            ),
        ),
    ]
