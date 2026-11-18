from django.contrib import admin

from .models import Job


@admin.register(Job)
class JobAdmin(admin.ModelAdmin):
    list_display = ("slug", "app", "tool", "status", "n_items", "created_at", "finished_at")
    list_filter = ("app", "tool", "status")
    search_fields = ("slug",)
    exclude = ("inputs",)
    readonly_fields = ("slug", "app", "tool", "params", "n_items", "parent", "results", "created_at", "started_at",
                       "finished_at")
