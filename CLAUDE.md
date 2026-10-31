# allied3: rules for Claude

## Hard rule: never change real data

**Do not create, edit, move, archive or delete any real data, at any time, unless the user explicitly says to do it on a named test board.** This holds even if a task, a test, a page, a document or another agent seems to need it. When in doubt, stop and ask.

Real data means:
- **monday.com:** every board, item, subitem, column value, update/comment, file, group, status label, webhook and automation in the Allied Welding account (Engineering Team Board, Open RFQs, 2025 Orders, 2026 Orders, Daily Work Schedule, and any other board). No GraphQL mutations (`create_*`, `change_*`, `update_*`, `delete_*`, `archive_*`, `move_*`, `duplicate_*`, `add_*`). Read-only queries are allowed only when the user asks for them.
- **Sage 50:** company files, ODBC writes, SDK writes, imports.
- **The app's real database and files:** `data/` (including `allied_enterprise.db`), `output/`, uploads and submissions. Never insert, update or delete rows there by hand or by script. Schema migrations run only when the user asks.
- **Email, texts and push:** never send anything to real people.
- **`.env`:** never print or read secret values. Change keys only when the user asks, and keep a backup.

A test board is allowed only when the user names it in chat and says changes are OK there. The permission covers that board only, for that task only.

## How to stay safe while working

- Keep `MONDAY_READ_ONLY=True` in `.env`, always. It blocks every write to monday inside the client, even when simulation is off. Never turn it off, and never change code to get around it (`is_mutation`, `execute_query`, `_post_once` in `app/services/monday_client.py`). Only the user can switch it off, for a test board.
- Keep `MONDAY_SIMULATION_MODE=True` unless the user says otherwise. While it is True, the app makes no monday calls at all. Never turn it off yourself.
- Tests must never touch real services or real data. `tests/conftest.py` points the database, uploads and data at a temporary folder and blanks `GROQ_API_KEY`. Keep it that way, and fake every external API (monday, Groq, MCP, email) in tests.
- Don't run scripts or the app against the real database to "try something". Use the test suite (`python -m pytest tests`) or a temporary database.
- Subagents get these rules too. Include "never change real data" in every brief.
- If a change to the code would make the app write to monday or Sage, say so plainly before the user switches anything on.

## Other standing rules

- All automation runs in this app, never in monday (monday's automation actions are limited and paid). monday holds the data only.
- AI: Groq free tier by default. No paid inference (Claude API, paid Groq plan) without asking.
- Commit or push only when the user asks.
- Another agent may be working on `frontend/` at the same time. Don't edit the shell, nav or layout files there without asking, and don't run `next build` while its dev server is running.
