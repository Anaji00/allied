"""GET /boards/{id} (the payload the Boards page reads) carries family and tags beside each row for staff, and nothing extra for a viewer. Temporary database; monday is faked."""
from app.services import organize


def test_board_view_annotates_rows_for_staff_only(monkeypatch):
    seen = []
    monkeypatch.setattr(organize, "annotate_rows", lambda items, *a, **k: seen.append(len(items)))
    items = [{"id": "1", "name": "A", "cells": {}}]
    organize.annotate_board_rows({"role": "VIEWER"}, "b1", items)
    assert seen == []
    organize.annotate_board_rows({"role": "OPERATOR"}, "b1", items)
    assert seen == [1]
