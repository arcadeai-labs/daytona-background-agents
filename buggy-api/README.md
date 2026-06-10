# buggy-api

A small Python API with an intentional pagination bug for demo purposes.

## The Bug

`src/handler.py` has an off-by-one error in the `get_page()` function. Page 2 duplicates items from page 1.

## Run Tests

```bash
pip install -r requirements.txt
pytest tests/ -v
```

The test `test_page_two_starts_at_item_11` will fail, exposing the bug.
