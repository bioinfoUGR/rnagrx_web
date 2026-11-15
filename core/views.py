from django.shortcuts import render


def landing(request):
    """Temporary landing page; the full presentation of the group's services comes later."""
    return render(request, "core/landing.html")
