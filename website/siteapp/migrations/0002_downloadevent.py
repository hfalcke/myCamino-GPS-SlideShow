from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("siteapp", "0001_initial")]

    operations = [
        migrations.CreateModel(
            name="DownloadEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("requested_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("ip_digest", models.CharField(blank=True, max_length=64)),
                ("user_agent", models.TextField(blank=True)),
                ("request_method", models.CharField(blank=True, max_length=12)),
                ("request_uri", models.TextField(blank=True)),
                ("range_header", models.CharField(blank=True, max_length=255)),
                ("registration", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="download_events", to="siteapp.betaregistration")),
                ("release", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="download_events", to="siteapp.release")),
            ],
            options={"ordering": ("-requested_at",)},
        ),
    ]
