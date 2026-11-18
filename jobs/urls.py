from django.urls import path

from . import uploads

app_name = "jobs"
urlpatterns = [
    path("upload/start/", uploads.start, name="upload_start"),
    path("upload/<str:token>/", uploads.status, name="upload_status"),
    path("upload/<str:token>/chunk/", uploads.chunk, name="upload_chunk"),
    path("upload/<str:token>/finish/", uploads.finish, name="upload_finish"),
    path("upload/<str:token>/cancel/", uploads.cancel, name="upload_cancel"),
]
