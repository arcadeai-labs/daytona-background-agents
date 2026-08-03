from .models import Item

# Simulated database of 25 items
ITEMS = [Item(id=i, name=f"Item {i}") for i in range(1, 26)]


def get_page(page: int, limit: int = 10) -> list[Item]:
    """Return a page of items. Pages are 1-indexed."""
    if page < 1:
        return []
    # Pages are 1-indexed, so page 1 must start at offset 0.
    offset = (page - 1) * limit
    return ITEMS[offset : offset + limit]
