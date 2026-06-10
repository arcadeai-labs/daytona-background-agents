from .models import Item

# Simulated database of 25 items
ITEMS = [Item(id=i, name=f"Item {i}") for i in range(1, 26)]


def get_page(page: int, limit: int = 10) -> list[Item]:
    """Return a page of items. Pages are 1-indexed."""
    if page < 1:
        return []
    # BUG: offset = page * limit produces duplicates on page 2
    # Fix: offset = (page - 1) * limit
    offset = page * limit
    return ITEMS[offset : offset + limit]
