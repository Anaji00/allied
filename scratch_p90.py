def sub(p, o, n):
    s = open(p, encoding="utf-8").read(); assert o in s, (p, o[:80]); open(p, "w", encoding="utf-8").write(s.replace(o, n, 1))


p = "app/services/assistant/board_tools.py"
# 2. no fuzzy auto-pick of a board
sub(p, '''    close = difflib.get_close_matches(want, list(names.values()), n=2, cutoff=0.6)
    hits = [i for i, n in names.items() if n in close]
    if len(hits) == 1:
        return str(hits[0])
    raise ValueError(f"I don't know a board called '{raw[:40]}'. The boards are: " + ", ".join(b["name"] for b in rows) + ".")''', '''    close = difflib.get_close_matches(want, list(names.values()), n=2, cutoff=0.6)      # a close spelling is only offered, never picked
    maybe = " Did you mean " + " or ".join(next(b["name"] for b in rows if _norm(b["name"]) == n) for n in close) + "?" if close else ""
    raise ValueError(f"I don't know a board called '{raw[:40]}'.{maybe} The boards are: " + ", ".join(b["name"] for b in rows) + ".")''')
# 4. stronger evidence for a row
sub(p, '''def find_items(board_id: str, text: str, limit: int = 5) -> List[Dict[str, Any]]:''', '''def _ranked(board_id: str, text: str, limit: int = 5) -> List[tuple]:''')
sub(p, '''    scored.sort(key=lambda p: -p[0])
    return [it for _, it in scored[:limit]]''', '''    scored.sort(key=lambda p: -p[0])
    return scored[:limit]


def find_items(board_id: str, text: str, limit: int = 5) -> List[Dict[str, Any]]:
    """Rows of this board that match what was said, best first."""
    return [it for _, it in _ranked(board_id, text, limit)]''')
sub(p, '''    hits = find_items(board_id, str(ref))
    if not hits:
        raise ValueError(f"No row on {b.get('name')} matches '{str(ref)[:40]}'.")
    top = hits[0]
    if len(hits) == 1 or _norm(top.get("name")) == _norm(ref) and _norm(hits[1].get("name")) != _norm(ref):
        return top
    raise ValueError("Which row? " + " | ".join(_row_text(b, h) for h in hits[:5]))''', '''    ranked = _ranked(board_id, str(ref))
    if not ranked:
        raise ValueError(f"No row on {b.get('name')} matches '{str(ref)[:40]}'.")
    hits = [it for _, it in ranked]
    top_score, top = ranked[0]
    clear = len(ranked) == 1 or (top_score >= 90 and ranked[1][0] < 90)
    strong = top_score >= 60 or (top_score >= 50 and len(_norm(ref)) >= 5)       # a name part, or a specific value (a PO number); not "ok" or a near spelling
    if clear and strong:
        return top
    raise ValueError(("Is it this row? " if len(hits) == 1 else "Which row? ") + " | ".join(_row_text(b, h) for h in hits[:5]))''')
# 7. cap long cell text
sub(p, '''    vals = [f"{cols.get(k, k)}: {v}" for k, v in (item.get("values") or {}).items() if str(v or "").strip()][:3]''', '''    vals = [f"{cols.get(k, k)}: {str(v)[:60]}" for k, v in (item.get("values") or {}).items() if str(v or "").strip()][:3]''')
# 3. pin what was resolved at proposal time
sub(p, '''    board = resolve_board(str(a.get("board") or ""))
    item = one_item(board, a.get("item_id") or a.get("item"))
    monday_push._column(_mirror(board), str(a.get("column") or ""))
    monday_push._clean_value(a.get("value"))''', '''    board = resolve_board(str(a.get("board") or ""))
    item = one_item(board, a.get("item_id") or a.get("item"))
    col = monday_push._column(_mirror(board), str(a.get("column") or ""))
    monday_push._clean_value(a.get("value"))
    a["board"], a["item_id"], a["column"] = board, str(item["id"]), col["id"]            # pinned: the card and the change are about the same row''')
sub(p, '''    board = resolve_board(str(a.get("board") or ""))
    if not str(a.get("item_name") or "").strip():
        raise ValueError("Give the new row a name.")
    for k, v in (a.get("fields") if isinstance(a.get("fields"), dict) else {}).items():
        monday_push._column(_mirror(board), str(k))
        monday_push._clean_value(v)''', '''    board = resolve_board(str(a.get("board") or ""))
    if not str(a.get("item_name") or "").strip():
        raise ValueError("Give the new row a name.")
    fields = {}
    for k, v in (a.get("fields") if isinstance(a.get("fields"), dict) else {}).items():
        col = monday_push._column(_mirror(board), str(k))
        monday_push._clean_value(v)
        fields[col["id"]] = v
    a["board"] = board
    if fields:
        a["fields"] = fields''')
sub(p, '''    board = resolve_board(str(a.get("board") or ""))
    one_item(board, a.get("item_id") or a.get("item"))
    if not str(a.get("text") or "").strip():
        raise ValueError("Write the comment first.")''', '''    board = resolve_board(str(a.get("board") or ""))
    item = one_item(board, a.get("item_id") or a.get("item"))
    if not str(a.get("text") or "").strip():
        raise ValueError("Write the comment first.")
    a["board"], a["item_id"] = board, str(item["id"])''')
# low: card column matches what is staged (same ambiguity rule)
sub(p, '''        col = next((c for c in b.get("columns", []) if c["id"] == a.get("column") or c["title"].casefold() == str(a.get("column") or "").casefold()), None)''', '''        from app.services import monday_push

        col = monday_push._column(b, str(a.get("column") or ""))''')
sub(p, '''f"Row: {item['name']}\\nColumn: {col['title'] if col else a.get('column')}\\n"''', '''f"Row: {item['name']}\\nColumn: {col['title']}\\n"''')
sub(p, '''        old = (item.get("values") or {}).get(col["id"]) if col else None''', '''        old = (item.get("values") or {}).get(col["id"])''')
# new-row card shows column titles
sub(p, '''summarize=lambda a: f"New row on {a.get('board')}: {a.get('item_name')}\\n" + "\\n".join(f"{k}: {v}" for k, v in (a.get("fields") or {}).items()) + "\\n(staged, not sent to monday)"),''', '''summarize=lambda a: _summary_row(a)),''')
sub(p, '''def stage_cell_edit(args''', '''def _summary_row(a: Dict[str, Any]) -> str:
    try:
        b = _mirror(resolve_board(str(a.get("board") or "")))
        cols = _cols(b)
        lines = "\\n".join(f"{cols.get(k, k)}: {v}" for k, v in (a.get("fields") or {}).items())
        return f"New row on {b.get('name')}: {a.get('item_name')}\\n{lines}\\n(staged, not sent to monday)"
    except Exception:  # noqa: BLE001
        return f"New row on {a.get('board')}: {a.get('item_name')} (staged, not sent to monday)"


def stage_cell_edit(args''')
sub(p, '''    return f"Comment on {a.get('board')} / {a.get('item_id')}:''', '''    return f"Comment on {a.get('board')} / {a.get('item_id')}:''') if False else None
