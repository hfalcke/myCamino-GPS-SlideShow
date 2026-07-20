import datetime
from django.db import migrations, models


def add_initial_release(apps, schema_editor):
    Release = apps.get_model("siteapp", "Release")
    Release.objects.create(
        label="Beta — 20 July 2026",
        file_name="myCamino-GPS-Track-Show.dmg",
        release_date=datetime.date(2026, 7, 20),
        file_size=183692319,
        sha256="471145b210890bef3beb11b2f5564a8ecba5c9959e03c505ff8c29dd5bca9a29",
        source_url="https://github.com/hfalcke/myCamino-GPS-SlideShow",
        is_active=True,
    )


class Migration(migrations.Migration):
    initial = True
    dependencies = []
    operations = [
        migrations.CreateModel(name="BetaRegistration", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("email", models.EmailField(max_length=254, unique=True)), ("consent_at", models.DateTimeField()),
            ("verified_at", models.DateTimeField(blank=True, null=True)), ("token_digest", models.CharField(blank=True, db_index=True, max_length=64)),
            ("token_expires_at", models.DateTimeField(blank=True, null=True)), ("verification_sent_at", models.DateTimeField(blank=True, null=True)),
            ("last_download_at", models.DateTimeField(blank=True, null=True)), ("download_count", models.PositiveIntegerField(default=0)),
            ("ip_digest", models.CharField(blank=True, max_length=64)), ("is_active", models.BooleanField(default=True)),
            ("created_at", models.DateTimeField(auto_now_add=True)), ("updated_at", models.DateTimeField(auto_now=True)),
        ], options={"ordering": ("-created_at",)}),
        migrations.CreateModel(name="ContactMessage", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("name", models.CharField(max_length=120)), ("email", models.EmailField(max_length=254)), ("subject", models.CharField(max_length=160)),
            ("message", models.TextField(max_length=5000)), ("consent_at", models.DateTimeField()), ("delivered_at", models.DateTimeField(blank=True, null=True)),
            ("delivery_error", models.TextField(blank=True)), ("admin_notes", models.TextField(blank=True)), ("ip_digest", models.CharField(blank=True, max_length=64)),
            ("created_at", models.DateTimeField(auto_now_add=True)),
        ], options={"ordering": ("-created_at",)}),
        migrations.CreateModel(name="Release", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("label", models.CharField(max_length=100)), ("file_name", models.CharField(max_length=200)), ("release_date", models.DateField()),
            ("file_size", models.PositiveBigIntegerField()), ("sha256", models.CharField(max_length=64)),
            ("source_url", models.URLField(default="https://github.com/hfalcke/myCamino-GPS-SlideShow")), ("is_active", models.BooleanField(default=False)),
            ("created_at", models.DateTimeField(auto_now_add=True)),
        ], options={"ordering": ("-release_date", "-created_at")}),
        migrations.RunPython(add_initial_release, migrations.RunPython.noop),
    ]
