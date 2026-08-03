from src.handler import get_page


def test_page_one_starts_at_item_1():
    page = get_page(1)
    assert page[0].id == 1, f"Expected first item id=1, got id={page[0].id}"
    assert len(page) == 10


def test_page_two_starts_at_item_11():
    """Page 2 should start where page 1 left off - no duplicates."""
    page1 = get_page(1)
    page2 = get_page(2)
    last_on_page1 = page1[-1].id
    first_on_page2 = page2[0].id
    assert first_on_page2 == last_on_page1 + 1, (
        f"Expected item {last_on_page1 + 1} on page 2, "
        f"got item {first_on_page2} - duplicate from page 1"
    )


def test_all_pages_cover_all_items():
    """Three pages of 10 should cover all 25 items with no gaps."""
    all_ids = []
    for p in range(1, 4):
        all_ids.extend(item.id for item in get_page(p))
    assert all_ids == list(range(1, 26)), f"Expected items 1-25, got {all_ids}"
