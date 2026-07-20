from django.db import models


class BetaRegistration(models.Model):
    email = models.EmailField(unique=True)
    consent_at = models.DateTimeField()
    verified_at = models.DateTimeField(null=True, blank=True)
    token_digest = models.CharField(max_length=64, blank=True, db_index=True)
    token_expires_at = models.DateTimeField(null=True, blank=True)
    verification_sent_at = models.DateTimeField(null=True, blank=True)
    last_download_at = models.DateTimeField(null=True, blank=True)
    download_count = models.PositiveIntegerField(default=0)
    ip_digest = models.CharField(max_length=64, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self):
        return self.email


class ContactMessage(models.Model):
    name = models.CharField(max_length=120)
    email = models.EmailField()
    subject = models.CharField(max_length=160)
    message = models.TextField(max_length=5000)
    consent_at = models.DateTimeField()
    delivered_at = models.DateTimeField(null=True, blank=True)
    delivery_error = models.TextField(blank=True)
    admin_notes = models.TextField(blank=True)
    ip_digest = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.subject} — {self.email}"


class Release(models.Model):
    label = models.CharField(max_length=100)
    file_name = models.CharField(max_length=200)
    release_date = models.DateField()
    file_size = models.PositiveBigIntegerField()
    sha256 = models.CharField(max_length=64)
    source_url = models.URLField(default="https://github.com/hfalcke/myCamino-GPS-SlideShow")
    is_active = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-release_date", "-created_at")

    def __str__(self):
        return self.label
