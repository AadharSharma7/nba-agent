import sys

import pytest

from nba_agent import cli
from nba_agent.config import BACKEND_ROOT, REPOSITORY_ROOT, Settings


@pytest.mark.parametrize("cwd", [REPOSITORY_ROOT, BACKEND_ROOT])
def test_repository_paths_are_independent_of_working_directory(monkeypatch, cwd):
    monkeypatch.chdir(cwd)
    settings = Settings(
        _env_file=None,
        data_dir="data/raw/kaggle/v515",
        manifest_path="audit/reports/snapshot_manifest.json",
    )
    assert settings.data_dir == REPOSITORY_ROOT / "data/raw/kaggle/v515"
    assert settings.manifest_path == REPOSITORY_ROOT / "audit/reports/snapshot_manifest.json"
    assert Settings.model_config["env_file"] == BACKEND_ROOT / ".env"


def test_absolute_data_paths_are_preserved(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings = Settings(_env_file=None, data_dir=tmp_path, manifest_path=tmp_path / "manifest.json")
    assert settings.data_dir == tmp_path
    assert settings.manifest_path == tmp_path / "manifest.json"


def test_setup_env_uses_backend_location_and_preserves_existing_file(tmp_path, monkeypatch):
    backend = tmp_path / "backend"
    backend.mkdir()
    (backend / ".example.env").write_text("OPENAI_API_KEY=placeholder\n")
    monkeypatch.setattr(cli, "BACKEND_ROOT", backend)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["nba-agent", "setup-env"])
    cli.main()
    target = backend / ".env"
    assert target.read_text() == "OPENAI_API_KEY=placeholder\n"
    assert target.stat().st_mode & 0o777 == 0o600
    assert not (tmp_path / ".env").exists()
    target.write_text("OPENAI_API_KEY=existing-test-value\n")
    cli.main()
    assert target.read_text() == "OPENAI_API_KEY=existing-test-value\n"
