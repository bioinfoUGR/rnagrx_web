from django.contrib import admin

from .models import Cart, FacetDefinition, IndexBuild, Selection


@admin.register(FacetDefinition)
class FacetDefinitionAdmin(admin.ModelAdmin):
    list_display = ("key", "label", "group", "widget", "enabled", "open_by_default", "order", "top_n")
    list_editable = ("label", "group", "enabled", "open_by_default", "order", "top_n")
    list_filter = ("enabled", "widget", "group")


@admin.register(IndexBuild)
class IndexBuildAdmin(admin.ModelAdmin):
    list_display = ("id", "status", "started_at", "finished_at", "n_experiments", "n_studies")
    readonly_fields = [f.name for f in IndexBuild._meta.fields]


@admin.register(Selection)
class SelectionAdmin(admin.ModelAdmin):
    list_display = ("slug", "name", "n_experiments", "created_at")
    search_fields = ("slug", "name")
    exclude = ("exp_ids",)


@admin.register(Cart)
class CartAdmin(admin.ModelAdmin):
    list_display = ("id", "created_at", "updated_at", "version")
