from django.contrib import admin

from .models import DataFile, Dataset, Job, Sample, Upload


@admin.register(Job)
class JobAdmin(admin.ModelAdmin):
    list_display = ("slug", "app", "tool", "status", "n_items", "created_at", "finished_at")
    list_filter = ("app", "tool", "status")
    search_fields = ("slug",)
    exclude = ("inputs",)
    readonly_fields = ("slug", "app", "tool", "params", "n_items", "parent", "results", "created_at", "started_at",
                       "finished_at")


@admin.register(Upload)
class UploadAdmin(admin.ModelAdmin):
    list_display = ("token", "filename", "size", "received", "status", "file_format", "job", "created_at")
    list_filter = ("status",)
    search_fields = ("token", "filename")
    readonly_fields = [f.name for f in Upload._meta.fields]


class DataFileInline(admin.TabularInline):
    model = DataFile
    fields = ("tag", "name", "size", "file_format", "source", "status", "message")
    readonly_fields = fields
    extra = 0
    can_delete = False


@admin.register(Dataset)
class DatasetAdmin(admin.ModelAdmin):
    list_display = ("slug", "title", "app", "created_at", "last_used_at", "deleted_at")
    list_filter = ("app",)
    search_fields = ("slug", "title")
    readonly_fields = ("slug", "app", "session_key", "created_at", "deleted_at")
    inlines = [DataFileInline]


@admin.register(Sample)
class SampleAdmin(admin.ModelAdmin):
    list_display = ("name", "dataset", "position")
    search_fields = ("name", "dataset__slug")
    raw_id_fields = ("dataset",)
