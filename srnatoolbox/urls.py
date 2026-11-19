from django.urls import path

from jobs import views as job_views

from . import views, views_bench, views_data, views_de

app_name = "srnatoolbox"
urlpatterns = [
    path("", views.home, name="home"),
    path("about/", views.about, name="about"),
    path("tools/<slug:key>/", views.tool_view, name="tool"),
    path("species/search/", views_bench.species_search, name="species_search"),
    path("job/<str:slug>/", views_bench.job_view, name="job"),
    path("job/<str:slug>/file/<str:name>", job_views.job_file, {"app": "srnatoolbox"}, name="job_file"),
    path("job/<str:slug>/sample/<str:name>/", views_bench.sample_view, name="job_sample"),
    path("job/<str:slug>/sample/<str:name>/zip/", views_bench.sample_zip, name="job_sample_zip"),
    path("job/<str:slug>/sample/<str:name>/file/<path:path>", views_bench.sample_file, name="job_sample_file"),
    path("job/<str:slug>/de/", views_de.srnade_view, name="srnade"),
    path("job/<str:slug>/de/pca/", views_de.srnade_pca, name="srnade_pca"),
    path("job/<str:slug>/de/file/<path:path>", views_de.de_file, name="de_file"),
    # datasets
    path("data/", views_data.data_home, name="data"),
    path("data/new/", views_data.data_new, name="data_new"),
    path("data/<str:slug>/", views_data.dataset_view, name="dataset"),
    path("data/<str:slug>/transfers/", views_data.dataset_transfers, name="dataset_transfers"),
    path("data/<str:slug>/transfers/clear/", views_data.dataset_transfers_clear, name="dataset_transfers_clear"),
    path("data/<str:slug>/links/", views_data.dataset_links, name="dataset_links"),
    path("data/<str:slug>/samples/", views_data.dataset_samples, name="dataset_samples"),
    path("data/<str:slug>/samples/edit/", views_data.dataset_cells, name="dataset_cells"),
    path("data/<str:slug>/samples/remove/", views_data.dataset_remove, name="dataset_remove"),
    path("data/<str:slug>/columns/", views_data.dataset_columns, name="dataset_columns"),
    path("data/<str:slug>/sheet.<str:fmt>", views_data.dataset_sheet, name="dataset_sheet"),
    path("data/<str:slug>/sheet/upload/", views_data.dataset_sheet_upload, name="dataset_sheet_upload"),
    path("data/<str:slug>/sheet/apply/", views_data.dataset_sheet_apply, name="dataset_sheet_apply"),
    path("data/<str:slug>/rename/", views_data.dataset_rename, name="dataset_rename"),
    path("data/<str:slug>/delete/", views_data.dataset_delete, name="dataset_delete"),
]
