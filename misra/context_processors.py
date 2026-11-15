from .cart import SESSION_KEY, cart_count, get_cart


def cart(request):
    """Cart size for the navigation badge (only miSRA pages; no DB query without a cart)."""
    if not request.path_info.startswith("/misra/") or SESSION_KEY not in request.session:
        return {}
    return {"cart_count": cart_count(get_cart(request))}
