from django.urls import path

from . import views

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
    path("explore/", views.explore, name="explore"),
    path("about/", views.about, name="about"),
    path("docs/", views.docs, name="docs"),
]
