from django.contrib import admin, messages

from .models import BetaRegistration, ContactMessage, Release
from .services import deliver_contact


@admin.register(BetaRegistration)
class BetaRegistrationAdmin(admin.ModelAdmin):
    list_display = ("email", "verified_at", "download_count", "last_download_at", "is_active", "created_at")
    list_filter = ("is_active", "verified_at", "created_at")
    search_fields = ("email",)
    readonly_fields = ("consent_at", "verified_at", "verification_sent_at", "last_download_at", "download_count", "ip_digest", "created_at", "updated_at")
    actions = ("deactivate",)

    @admin.action(description="Deactivate selected registrations")
    def deactivate(self, request, queryset):
        queryset.update(is_active=False, token_digest="")


@admin.register(ContactMessage)
class ContactMessageAdmin(admin.ModelAdmin):
    list_display = ("subject", "email", "created_at", "delivered_at")
    list_filter = ("created_at", "delivered_at")
    search_fields = ("name", "email", "subject", "message")
    readonly_fields = ("name", "email", "subject", "message", "consent_at", "delivered_at", "delivery_error", "ip_digest", "created_at")
    actions = ("retry_delivery",)

    @admin.action(description="Retry email delivery")
    def retry_delivery(self, request, queryset):
        delivered = sum(deliver_contact(item) for item in queryset)
        self.message_user(request, f"Delivered {delivered} of {queryset.count()} messages.", messages.SUCCESS if delivered else messages.WARNING)


@admin.register(Release)
class ReleaseAdmin(admin.ModelAdmin):
    list_display = ("label", "release_date", "file_name", "file_size", "is_active")
    list_filter = ("is_active", "release_date")
    search_fields = ("label", "file_name", "sha256")

    def save_model(self, request, obj, form, change):
        if obj.is_active:
            Release.objects.exclude(pk=obj.pk).update(is_active=False)
        super().save_model(request, obj, form, change)
