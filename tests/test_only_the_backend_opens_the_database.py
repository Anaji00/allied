"""Only the backend service may open the real database file: the flag is in the backend's compose service and nowhere else, and the warning is in the README. No file in data/ is opened."""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def test_the_allow_flag_is_set_for_the_backend_service_only():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    holders = [name for name, svc in compose["services"].items() if "ALLIED_ALLOW_REAL_DB" in (svc.get("environment") or {})]
    assert holders == ["backend"] and str(compose["services"]["backend"]["environment"]["ALLIED_ALLOW_REAL_DB"]) == "1"


def test_the_flag_is_not_set_in_the_environment_files_or_code_outside_the_guard():
    for name in (".env.example",):
        f = ROOT / name
        if f.exists():
            assert "ALLIED_ALLOW_REAL_DB" not in f.read_text(encoding="utf-8")
    hits = [str(p.relative_to(ROOT)) for p in list((ROOT / "app").rglob("*.py")) + list((ROOT / "scripts").rglob("*.py")) + list((ROOT / "migrations").rglob("*.py"))
            if "ALLIED_ALLOW_REAL_DB" in p.read_text(encoding="utf-8", errors="ignore") and "setdefault" in p.read_text(encoding="utf-8", errors="ignore")]
    assert hits == []                                                   # nothing in the code switches the flag on for itself


def test_the_readme_warns_and_the_backup_script_says_where_to_run_it():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "Never open `data/allied_enterprise.db` from Windows" in readme and "only process allowed to open the real file" in readme
    assert "docker compose exec backend python scripts/backup.py" in readme and "INSIDE the container" in (ROOT / "scripts" / "backup.py").read_text(encoding="utf-8")
