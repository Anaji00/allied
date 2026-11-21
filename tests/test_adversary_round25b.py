"""Round 25b (adversary): re-attack of the file: URI fix in the real-database guard. Decisions on URL strings only; nothing is opened. xfail(strict=False, reason="ADV25b-Tn ...") marks holes."""
import os
from pathlib import Path

import pytest

from app.core import database
from tests.test_adversary_round25 import REAL, R, refused

ENC = R.replace(":", "%3A")


@pytest.mark.parametrize("url", [f"sqlite:///FILE:{R}/data/allied_enterprise.db?uri=true", f"sqlite:///File:{R}/data/allied_enterprise.db?uri=TRUE", f"sqlite:///file:{R}/data/allied_enterprise.db?uri=1&mode=rwc",
                                 f"sqlite:///file:///{ENC}/data/allied_enterprise.db?uri=true", f"sqlite:///file://localhost/{R}/data/allied_enterprise.db?uri=true",
                                 f"sqlite:///file:{R}/data/%2E%2E/data/allied_enterprise.db?uri=true", f"sqlite:///file:{R}/data/allied_enterprise.db?mode=ro&immutable=1&uri=true",
                                 f"sqlite:///file:{R}/data/allied_enterprise.db#frag?uri=true", f"sqlite:///file:{R}/data/allied_enterprise.db?uri=true&vfs=win32",
                                 f"sqlite:///file:{R}/data/allied_enterprise%2Edb?uri=true", f"sqlite:///file:{R}/data/ALLIED_ENTERPRISE.DB?uri=true", f"sqlite:///{R}/data/allied_enterprise.db?uri=true"],
                         ids=["FILE-upper", "File-mixed-TRUE", "uri-1", "encoded-colon", "localhost", "encoded-dotdot", "immutable", "fragment", "vfs", "encoded-dot", "name-upper", "plain-with-uri-flag"])
def test_more_file_uri_spellings_of_the_real_file_are_refused(url):
    assert refused(url)


def test_drive_relative_and_relative_file_uris_from_the_project_folder_are_refused(monkeypatch):
    monkeypatch.chdir(REAL.parent.parent)
    for url in ("sqlite:///file:data/allied_enterprise.db?uri=true", "sqlite:///file:./data/allied_enterprise.db?uri=true", "sqlite:///file:data%2Fallied_enterprise.db?uri=true",
                f"sqlite:///file:{os.path.splitdrive(str(REAL))[0]}data/allied_enterprise.db?uri=true" if os.name == "nt" else "sqlite:///file:data/allied_enterprise.db?uri=true"):
        assert refused(url), url


@pytest.mark.parametrize("url", ["sqlite:///file::memory:?cache=shared&uri=true", "sqlite:///file:memdb1?mode=memory&cache=shared&uri=true", "sqlite://"], ids=["memory-shared", "named-memory", "bare"])
def test_in_memory_file_uris_are_not_refused(url):
    if refused(url):
        pytest.xfail("ADV25b-T1 an in-memory SQLite URI is refused as if it might be the real file")


def test_a_file_uri_inside_the_temp_folder_is_allowed(tmp_path):
    p = tmp_path / "scratch.db"
    assert not refused(f"sqlite:///file:{p.as_posix()}?uri=true&mode=rwc")
    assert not refused(f"sqlite:///file:///{p.as_posix()}?uri=true")
    assert not refused(f"sqlite:///file:{p.as_posix().replace('scratch', 'scr%61tch')}?uri=true")
