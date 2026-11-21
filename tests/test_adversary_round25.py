"""Round 25 (adversary): the real-database guard (app/core/database.py assert_may_use_database, _real_database_files, _sqlite_path; migrations/env.py; tests/conftest.py assert_under_temp).
Every check is a DECISION of the guard function on a URL string, compared with the file the operating system / SQLAlchemy / SQLite would really open for that spelling. Nothing is ever opened,
created or migrated: no engine, no init_database, no alembic, no connection. The real path is only computed as text. xfail(strict=False, reason="ADV25-Tn ...") marks holes."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import unquote

import pytest
from sqlalchemy.engine import make_url

from app.core import database

ROOT = Path(__file__).resolve().parent.parent
REAL = ROOT / "data" / "allied_enterprise.db"
R = ROOT.as_posix()
IS_WIN = os.name == "nt"


def _os_normal(path: str) -> str:
    """What Windows does to a name before it reaches the file system: strips the stream suffix, trailing dots and spaces and the extended-length prefix."""
    p = str(path)
    if IS_WIN:
        for pre in ("\\\\?\\", "//?/"):
            if p.startswith(pre):
                p = p[len(pre):]
        if p.upper().endswith("::$DATA"):
            p = p[:-7]
        head, sep, tail = p.replace("/", "\\").rpartition("\\")
        tail = tail.rstrip(". ")
        p = head + sep + tail
    return os.path.normcase(os.path.abspath(p))


def opens(url: str):
    """The path SQLAlchemy's SQLite driver would hand to sqlite3 (and the OS would open) for this URL, normalised; None for memory."""
    u = make_url(url)
    db = u.database
    if not db or db == ":memory:":
        return None
    uri = str(u.query.get("uri", "")).lower() in ("true", "1")
    if uri and db.startswith("file:"):
        rest = unquote(db[5:])
        if rest.startswith("///"):
            rest = rest[3:]
        elif rest.startswith("//"):
            rest = rest[2:].split("/", 1)[-1] if "/" in rest[2:] else rest[2:]
        db = rest
    return _os_normal(db)


REAL_OS = _os_normal(str(REAL))


def refused(url: str) -> bool:
    try:
        database.assert_may_use_database(url)
        return False
    except RuntimeError:
        return True


SPELLINGS = {
    "plain": f"sqlite:///{R}/data/allied_enterprise.db",
    "backslashes": "sqlite:///" + str(REAL),
    "upper": "sqlite:///" + str(REAL).upper(),
    "lower": "sqlite:///" + str(REAL).lower(),
    "dotdot": f"sqlite:///{R}/app/../data/allied_enterprise.db",
    "dot": f"sqlite:///{R}/./data/./allied_enterprise.db",
    "double-slash": f"sqlite:///{R}//data//allied_enterprise.db",
    "mixed-slashes": "sqlite:///" + str(ROOT).replace("\\", "/") + "\\data/allied_enterprise.db",
    "query-mode-rw": f"sqlite:///{R}/data/allied_enterprise.db?mode=rw",
    "query-cache": f"sqlite:///{R}/data/allied_enterprise.db?cache=shared&timeout=10",
    "pysqlite-driver": f"sqlite+pysqlite:///{R}/data/allied_enterprise.db",
}
HOLES = {
    "file-uri-abs": f"sqlite:///file:{R}/data/allied_enterprise.db?uri=true&mode=rwc",
    "file-uri-triple-slash": f"sqlite:///file:///{R}/data/allied_enterprise.db?uri=true",
    "file-uri-encoded": f"sqlite:///file:{R}/data/allied%5Fenterprise.db?uri=true",
    "file-uri-encoded-dir": f"sqlite:///file:{R}/d%61ta/allied_enterprise.db?uri=true&mode=ro",
}
WIN_HOLES = {
    "trailing-dot": f"sqlite:///{R}/data/allied_enterprise.db.",
    "trailing-dots": f"sqlite:///{R}/data/allied_enterprise.db...",
    "trailing-space": f"sqlite:///{R}/data/allied_enterprise.db ",
    "dot-space": f"sqlite:///{R}/data/allied_enterprise.db. .",
    "data-stream": f"sqlite:///{R}/data/allied_enterprise.db::$DATA",
}


@pytest.mark.parametrize("name", sorted(SPELLINGS))
def test_ordinary_spellings_of_the_real_file_are_refused(name):
    url = SPELLINGS[name]
    assert opens(url) == REAL_OS, "the spelling must really mean the real file"
    assert refused(url)


@pytest.mark.parametrize("name", sorted(HOLES))
def test_t1_file_uri_spellings_of_the_real_file_are_refused(name):
    url = HOLES[name]
    assert opens(url) == REAL_OS, "the spelling must really mean the real file"
    assert refused(url)


@pytest.mark.parametrize("name", sorted(WIN_HOLES))
def test_windows_name_tricks_for_the_real_file_are_refused(name):
    if not IS_WIN:
        pytest.skip("Windows file-name rules")
    url = WIN_HOLES[name]
    assert opens(url) == REAL_OS, "the spelling must really mean the real file"
    assert refused(url)


def test_relative_spellings_from_any_working_directory_follow_the_cwd_like_sqlite_does(monkeypatch, tmp_path):
    for cwd in (ROOT, ROOT / "tests", ROOT / "data" if False else ROOT / "app", tmp_path):
        monkeypatch.chdir(cwd)
        rel = os.path.relpath(str(REAL), str(cwd)).replace("\\", "/")
        url = f"sqlite:///{rel}"
        assert opens(url) == REAL_OS
        assert refused(url), (cwd, url)
        assert not refused("sqlite:///data/allied_enterprise.db") or opens("sqlite:///data/allied_enterprise.db") == REAL_OS


def test_a_relative_name_that_is_not_the_real_file_from_another_cwd_is_allowed(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    assert opens("sqlite:///data/allied_enterprise.db") != REAL_OS
    assert not refused("sqlite:///data/allied_enterprise.db")


def test_a_symlink_or_junction_into_data_is_followed_and_refused(tmp_path):
    link = tmp_path / "lnk"
    try:
        os.symlink(str(REAL.parent), str(link), target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("cannot create a symlink here")
    assert refused(f"sqlite:///{(link / 'allied_enterprise.db').as_posix()}")


def test_t3_a_hard_link_to_the_guarded_file_is_refused(tmp_path, monkeypatch):
    real = tmp_path / "pretend_real" / "allied_enterprise.db"
    real.parent.mkdir()
    real.write_bytes(b"SQLite format 3\x00")
    link = tmp_path / "alias.db"
    try:
        os.link(str(real), str(link))
    except OSError:
        pytest.skip("no hard links here")
    monkeypatch.setattr(database, "_real_database_files", lambda: {os.path.normcase(str(real.resolve()))})
    monkeypatch.setitem(sys.modules, "pytest", sys.modules["pytest"])
    assert refused(f"sqlite:///{link.as_posix()}")


@pytest.mark.parametrize("url", ["sqlite:///:memory:", "sqlite://", "sqlite:///", "postgresql://u:p@h/db", "mysql+pymysql://u@h/db", "", "not a url"])
def test_non_files_are_allowed_without_error(url):
    assert not refused(url)


@pytest.mark.parametrize("name", ["data_test", "data", "datastore", "metadata", "allied_enterprise_data", "allied_enterprise.db.bak", "allied_enterprise.db-wal", "allied_enterprise_test.db", "allied_enterprise.db2"])
def test_a_temporary_database_with_data_in_its_name_is_allowed(name, tmp_path):
    folder = tmp_path / name
    folder.mkdir(exist_ok=True)
    assert not refused(f"sqlite:///{(folder / 'x.db').as_posix()}")
    assert not refused(f"sqlite:///{(tmp_path / (name + '.db')).as_posix()}")
    assert not refused(f"sqlite:///{(Path(tempfile.gettempdir()) / name / 'allied_demo.db').as_posix()}")


@pytest.mark.parametrize("name", ["allied_enterprise.db", "ALLIED_ENTERPRISE.DB"])
def test_a_scratch_file_with_the_real_name_in_a_temp_folder_is_not_the_real_file(name, tmp_path):
    assert not refused(f"sqlite:///{(tmp_path / name).as_posix()}")


def test_the_db_dir_override_makes_its_default_file_count_as_real_and_nothing_else(monkeypatch, tmp_path):
    from app.config import settings

    monkeypatch.setattr(settings, "DB_DIR", tmp_path)
    assert os.path.normcase(str((tmp_path / "allied_enterprise.db").resolve())) in database._real_database_files()
    assert refused(f"sqlite:///{(tmp_path / 'allied_enterprise.db').as_posix()}")
    assert not refused(f"sqlite:///{(tmp_path / 'allied_demo.db').as_posix()}")
    assert refused(f"sqlite:///{R}/data/allied_enterprise.db")                      # the project's own file stays guarded whatever DB_DIR says


def _run_guard(env_extra: dict, url: str, import_pytest: bool = False, fake_real: str = ""):
    """The guard in a fresh process that is NOT under pytest, with a FAKE real-file set: nothing real is named, opened or migrated."""
    code = ("import os,sys\n" + ("import pytest\n" if import_pytest else "") +
            "from app.core import database\n"
            f"database._real_database_files = lambda: {{os.path.normcase(os.path.abspath({fake_real!r}))}}\n"
            f"try:\n    database.assert_may_use_database({url!r})\n    print('ALLOWED')\nexcept RuntimeError as e:\n    print('REFUSED')\n")
    env = {k: v for k, v in os.environ.items() if k not in ("ALLIED_ALLOW_REAL_DB", "PYTEST_CURRENT_TEST", "DATABASE_URL")}
    env.update(env_extra)
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    return out.stdout.strip().splitlines()[-1] if out.stdout.strip() else out.stderr[-200:]


@pytest.mark.parametrize("flag,want", [(None, "REFUSED"), ("1", "ALLOWED"), ("0", "REFUSED"), ("", "REFUSED"), ("true", "REFUSED"), (" 1", "REFUSED"), ("1 ", "REFUSED"), ("01", "REFUSED"), ("yes", "REFUSED"),
                                       ("TRUE", "REFUSED"), ("1\n", "REFUSED")], ids=["unset", "one", "zero", "blank", "true", "space-one", "one-space", "zero-one", "yes", "TRUE", "one-newline"])
def test_only_the_exact_flag_value_one_allows_the_real_file_outside_pytest(flag, want, tmp_path):
    fake = str(tmp_path / "data" / "allied_enterprise.db")
    env = {} if flag is None else {"ALLIED_ALLOW_REAL_DB": flag}
    assert _run_guard(env, f"sqlite:///{Path(fake).as_posix()}", fake_real=fake) == want


def test_the_container_style_path_with_the_flag_is_allowed_and_without_it_refused(tmp_path):
    fake = "/app/data/allied_enterprise.db"
    assert _run_guard({"ALLIED_ALLOW_REAL_DB": "1"}, "sqlite:////app/data/allied_enterprise.db", fake_real=fake) == "ALLOWED"
    assert _run_guard({}, "sqlite:////app/data/allied_enterprise.db", fake_real=fake) == "REFUSED"
    assert _run_guard({"ALLIED_ALLOW_REAL_DB": "1"}, "sqlite:////app/data/other.db", fake_real=fake) == "ALLOWED"


def test_a_process_that_merely_imports_pytest_is_treated_as_a_test_even_with_the_flag(tmp_path):
    fake = str(tmp_path / "data" / "allied_enterprise.db")
    assert _run_guard({"ALLIED_ALLOW_REAL_DB": "1"}, f"sqlite:///{Path(fake).as_posix()}", import_pytest=True, fake_real=fake) == "REFUSED"
    assert _run_guard({"PYTEST_CURRENT_TEST": "x::y (call)", "ALLIED_ALLOW_REAL_DB": "1"}, f"sqlite:///{Path(fake).as_posix()}", fake_real=fake) == "REFUSED"


def test_a_script_that_sets_a_temp_url_is_never_refused_with_or_without_the_flag(tmp_path):
    fake = str(tmp_path / "data" / "allied_enterprise.db")
    other = tmp_path / "scratch" / "usecases.db"
    assert _run_guard({}, f"sqlite:///{other.as_posix()}", fake_real=fake) == "ALLOWED"
    assert _run_guard({"ALLIED_ALLOW_REAL_DB": "1"}, f"sqlite:///{other.as_posix()}", fake_real=fake) == "ALLOWED"


def test_a_blank_database_url_falls_back_to_the_default_which_is_guarded(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "DATABASE_URL", "")
    monkeypatch.setattr(settings, "DB_DIR", ROOT / "data")
    url = database._get_db_url()
    assert opens(url) == REAL_OS and refused(url)


def test_conftest_assert_under_temp_refuses_everything_outside_the_test_folder_and_accepts_its_own():
    from tests.conftest import _TEST_TMP, assert_under_temp

    for bad in (REAL, f"sqlite:///{REAL.as_posix()}", ROOT, Path(tempfile.gettempdir()), Path(tempfile.gettempdir()) / "other" / "x.db", f"sqlite:///{(ROOT / 'tests' / 'x.db').as_posix()}", Path("data") / "allied_enterprise.db"):
        with pytest.raises(RuntimeError):
            assert_under_temp(bad)
    assert_under_temp(_TEST_TMP / "x.db")
    assert_under_temp(f"sqlite:///{(_TEST_TMP / 'sub' / 'x.db').as_posix()}")


def test_conftest_check_judges_urls_with_queries_and_file_uris_safely(tmp_path):
    from tests.conftest import _TEST_TMP, assert_under_temp

    with pytest.raises(RuntimeError):
        assert_under_temp(f"sqlite:///file:{REAL.as_posix()}?uri=true")
    assert_under_temp(f"sqlite:///{(_TEST_TMP / 'x.db').as_posix()}?mode=rwc&timeout=5")


def test_alembic_env_runs_the_same_guard_before_it_touches_anything():
    text = (ROOT / "migrations" / "env.py").read_text(encoding="utf-8")
    assert "assert_may_use_database(_get_db_url())" in text
    assert text.index("assert_may_use_database(_get_db_url())") < text.index("config.set_main_option")


def test_docker_compose_sets_the_flag_for_the_backend_service_only():
    import re

    text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    hits = [m.start() for m in re.finditer(r"ALLIED_ALLOW_REAL_DB", text)]
    assert len(hits) == 1
    head = text[:hits[0]]
    service = re.findall(r"^  ([a-z0-9_-]+):\s*$", head, re.M)[-1]
    assert service in ("backend", "api", "app", "allied-backend") or "backend" in service, service


def test_t1b_a_relative_file_uri_from_the_project_folder_is_refused(monkeypatch):
    monkeypatch.chdir(ROOT)
    url = "sqlite:///file:data/allied_enterprise.db?uri=true&mode=rwc"
    assert opens(url) == REAL_OS
    assert refused(url)
