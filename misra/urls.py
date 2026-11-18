from django.urls import path, re_path

from jobs import views as job_views

from . import views, views_tools
from .api import views as api

app_name = "misra"
urlpatterns = [
    path("", views.home, name="home"),
    path("browse/", views.browse, name="browse"),
    path("facet/<slug:key>/", views.facet_values, name="facet_values"),
    path("taxonomy/", views.taxonomy, name="taxonomy"),
    path("experiment/<str:exp_id>/", views.experiment_detail, name="experiment"),
    path("study/<str:study_id>/", views.study_detail, name="study"),
    path("cart/", views.cart_view, name="cart"),
    path("cart/add/", views.cart_add, name="cart_add"),
    path("cart/remove/", views.cart_remove, name="cart_remove"),
    path("cart/keep/", views.cart_keep, name="cart_keep"),
    path("cart/clear/", views.cart_clear, name="cart_clear"),
    path("cart/save/", views.cart_save, name="cart_save"),
    path("cart/metadata.tsv", views.cart_tsv, name="cart_tsv"),
    path("selection/<str:slug>/", views.selection_view, name="selection"),
    path("selection/<str:slug>/metadata.tsv", views.selection_tsv, name="selection_tsv"),
    path("tools/", views_tools.tools_index, name="tools"),
    path("tools/check/", views_tools.check_accessions, name="check_accessions"),
    path("tools/<slug:key>/", views_tools.tool_view, name="tool"),
    path("job/<str:slug>/", job_views.job_view, {"app": "misra"}, name="job"),
    path("job/<str:slug>/file/<str:name>", job_views.job_file, {"app": "misra"}, name="job_file"),
    path("api/", api.api_help, name="api_help"),
    re_path(r"^api/v1/studies/(?P<accession>[A-Za-z0-9]+)/?$", api.studies, name="api_study"),
    re_path(r"^api/v1/selections/(?P<slug>[A-Za-z0-9_-]+)/?$", api.selections, name="api_selection"),
    re_path(r"^api/v1/(?P<name>[a-z-]+)/?$", api.dispatch, name="api"),
    path("explore/", views.explore, name="explore"),
    path("about/", views.about, name="about"),
    path("docs/", views.docs, name="docs"),
]
