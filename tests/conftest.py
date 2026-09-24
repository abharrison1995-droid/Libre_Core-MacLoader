"""Shared pytest fixtures and test configuration for MacLoader."""

from pathlib import Path
import pytest

from macloader.database.loader import Database, get_database

TESTS_DIR = Path(__file__).parent
FIXTURES_DIR = TESTS_DIR / "fixtures"


@pytest.fixture(autouse=True)
def _isolated_recovery_evidence(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Keep discovery evidence written by tests out of the user's workspace."""
    import macloader.workflow.service as workflow_service_module

    path: Path = tmp_path_factory.mktemp("recovery-evidence") / "private" / "recovery" / "discovery-evidence.json"
    monkeypatch.setattr(workflow_service_module, "DEFAULT_RECOVERY_EVIDENCE_PATH", path)
    return path


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES_DIR


@pytest.fixture
def db() -> Database:
    return get_database(reload=True)


@pytest.fixture
def test_db(db: Database) -> Database:
    return db


@pytest.fixture
def t480s_baseline_fixture() -> Path:
    return FIXTURES_DIR / "t480s" / "t480s_baseline.json"


@pytest.fixture
def t480s_touchscreen_fixture() -> Path:
    return FIXTURES_DIR / "t480s" / "t480s_touchscreen.json"


@pytest.fixture
def t480_igpu_fixture() -> Path:
    return FIXTURES_DIR / "t480" / "t480_igpu.json"


@pytest.fixture
def t480_mx150_fixture() -> Path:
    return FIXTURES_DIR / "t480" / "t480_mx150.json"


@pytest.fixture
def t480_pm981_fixture() -> Path:
    return FIXTURES_DIR / "t480" / "t480_pm981.json"


@pytest.fixture
def x1_carbon_fixture() -> Path:
    return FIXTURES_DIR / "unsupported" / "x1_carbon.json"


@pytest.fixture
def non_lenovo_fixture() -> Path:
    return FIXTURES_DIR / "unsupported" / "non_lenovo_dell.json"


@pytest.fixture
def incomplete_probe_fixture() -> Path:
    return FIXTURES_DIR / "unsupported" / "incomplete_probe.json"
