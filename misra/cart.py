"""The anonymous experiment cart: stored in rnagrx_web, its id kept in the session."""
from __future__ import annotations

import numpy as np
from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from .engine import Catalog
from .models import Cart, CartItem

SESSION_KEY = "misra_cart_id"
CHUNK = 5000


def get_cart(request, create: bool = False) -> Cart | None:
    cart_id = request.session.get(SESSION_KEY)
    cart = Cart.objects.filter(pk=cart_id).first() if cart_id else None
    if cart is None and create:
        cart = Cart.objects.create()
        request.session[SESSION_KEY] = cart.pk
    return cart


def cart_ids(cart: Cart | None) -> list[str]:
    if cart is None:
        return []
    key = f"misra:cart:{cart.pk}:{cart.version}"
    ids = cache.get(key)
    if ids is None:
        ids = list(CartItem.objects.filter(cart=cart).values_list("exp_id", flat=True))
        cache.set(key, ids, 600)
    return ids


def cart_count(cart: Cart | None) -> int:
    return len(cart_ids(cart)) if cart else 0


def cart_mask(cart: Cart | None, catalog: Catalog) -> np.ndarray:
    return catalog.mask_of_positions(catalog.positions_of(cart_ids(cart)))


def _touch(cart: Cart) -> None:
    Cart.objects.filter(pk=cart.pk).update(version=F("version") + 1, updated_at=timezone.now())
    cart.refresh_from_db()


def add(cart: Cart, exp_ids) -> tuple[int, bool]:
    """Add accessions; returns (number newly added, True if the cart limit cut the list)."""
    current = set(cart_ids(cart))
    new = [e for e in dict.fromkeys(exp_ids) if e not in current]
    room = max(0, settings.MISRA_CART_LIMIT - len(current))
    truncated = len(new) > room
    new = new[:room]
    if new:
        with transaction.atomic():
            for i in range(0, len(new), CHUNK):
                CartItem.objects.bulk_create([CartItem(cart=cart, exp_id=e) for e in new[i:i + CHUNK]],
                                             ignore_conflicts=True)
            _touch(cart)
    return len(new), truncated


def remove(cart: Cart, exp_ids) -> int:
    ids = list(dict.fromkeys(exp_ids))
    removed = 0
    if ids:
        with transaction.atomic():
            for i in range(0, len(ids), CHUNK):
                removed += CartItem.objects.filter(cart=cart, exp_id__in=ids[i:i + CHUNK]).delete()[0]
            _touch(cart)
    return removed


def keep_only(cart: Cart, exp_ids) -> int:
    keep = set(exp_ids)
    return remove(cart, [e for e in cart_ids(cart) if e not in keep])


def clear(cart: Cart) -> int:
    with transaction.atomic():
        n = CartItem.objects.filter(cart=cart).delete()[0]
        _touch(cart)
    return n
