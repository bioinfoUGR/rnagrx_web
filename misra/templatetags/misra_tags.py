from django import template

register = template.Library()


@register.filter
def reads(value):
    """12345678 -> 12.3 M"""
    if value is None or value == "":
        return "—"
    x = float(value)
    for unit, size in (("G", 1e9), ("M", 1e6), ("k", 1e3)):
        if x >= size:
            return f"{x / size:.1f} {unit}"
    return f"{x:.0f}"


@register.filter
def pct(value):
    if value is None or value == "":
        return "—"
    return f"{float(value):.1f}"


@register.filter
def na(value):
    return "—" if value in (None, "") else value


@register.filter
def get(mapping, key):
    try:
        return mapping.get(key)
    except AttributeError:
        return None


@register.filter
def plural(n, word):
    return f"{n:,} {word}{'' if n == 1 else 's'}"
