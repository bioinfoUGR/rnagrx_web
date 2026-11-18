from datetime import timedelta

from django.conf import settings
from django.contrib.sessions.models import Session
from django.core.management.base import BaseCommand
from django.utils import timezone

from misra.models import Cart


class Command(BaseCommand):
    help = ("Delete carts not used for MISRA_CART_MAX_AGE_DAYS days and expired sessions. "
            "Job results are removed by `manage.py jobs_cleanup`.")

    def handle(self, *args, **opts):
        now = timezone.now()
        n_carts, _ = Cart.objects.filter(updated_at__lt=now - timedelta(days=settings.MISRA_CART_MAX_AGE_DAYS)).delete()
        n_sessions, _ = Session.objects.filter(expire_date__lt=now).delete()
        self.stdout.write(f"deleted {n_carts} cart objects, {n_sessions} expired sessions")
