from django.urls import path

from jobs import views as job_views

from . import views

app_name = "srnatoolbox"
urlpatterns = [
    path("", views.home, name="home"),
    path("about/", views.about, name="about"),
    path("tools/<slug:key>/", views.tool_view, name="tool"),
    path("job/<str:slug>/", job_views.job_view, {"app": "srnatoolbox"}, name="job"),
    path("job/<str:slug>/file/<str:name>", job_views.job_file, {"app": "srnatoolbox"}, name="job_file"),
]
