import tomllib
from pathlib import Path


def test_paramiko_only_loaded_by_patched_ssh_extra():
    project = tomllib.loads(
        (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text()
    )["project"]
    direct = project["dependencies"]
    extras = project.get("optional-dependencies", {})
    assert not any(dep.lower().startswith("paramiko") for dep in direct)
    assert "ssh" in extras
    assert any(dep.startswith("paramiko>=5") for dep in extras["ssh"])
    assert all("paramiko" not in dep for dep in direct)


def test_common_docker_client_remains_required():
    project = tomllib.loads(
        (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text()
    )["project"]
    assert any(dep.startswith("docker>=") for dep in project["dependencies"])
