"""Direct tests of the real ``docker_to_dict`` trimming logic.

The existing ``test_server.py`` suite monkeypatches ``docker_to_dict`` with a
stub (so it only checks tool argument semantics). This file exercises the real
serialization — the summary shape, the full shape, the health-log cap, and the
image-ref fallback — with faithful docker-py models, plus one end-to-end MCP
call that proves ``list_containers`` defaults to the compact shape.
"""

import json

import pytest
from docker.models.containers import Container
from docker.models.images import Image
from docker.models.networks import Network
from docker.models.volumes import Volume
from mcp.client import Client

import mcp_server_docker.server as server_module
from mcp_server_docker.output_schemas import (
    HEALTH_LOG_ENTRY_CAP,
    _trim_health_log,
    docker_to_dict,
)
from mcp_server_docker.server import app

TRUNCATION_MARK = "\u2026 [truncated]"
BIG_OUTPUT = "<html><body>" + "A" * 2000 + "</body></html"


def _state(health=None, **over):
    base = {
        "Status": "running",
        "Running": True,
        "Paused": False,
        "Restarting": False,
        "OOMKilled": False,
        "Dead": False,
        "Pid": 2404,
        "ExitCode": 0,
        "Error": "",
        "StartedAt": "2026-09-15T15:55:05.343Z",
        "FinishedAt": "2026-09-15T15:54:25.209Z",
    }
    if health is not None:
        base["Health"] = health
    base.update(over)
    return base


def _container_attrs(
    name="app",
    image_ref="nginx:latest",
    health=None,
    mounts=None,
    config_image=None,
):
    return {
        "Id": "f8fac9ce6323be223c23e0bd6e2b192f22e418637a27ece7cbc0784fc7ed4963",
        "Name": "/" + name,
        "State": _state(health=health),
        "Config": {
            "Hostname": name + ".abc",
            "User": "",
            "Image": config_image if config_image is not None else image_ref,
            "Labels": {"com.example.foo": "bar"},
        },
        "RestartCount": 0,
        "NetworkSettings": {
            "Networks": {"bridge": {"IPAMConfig": None}},
            "Ports": {"80/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8080"}]},
        },
        "Mounts": mounts
        or [
            {
                "Type": "volume",
                "Name": "app_data",
                "Source": "/var/lib/docker/volumes/app_data/_data",
                "Destination": "/data",
                "Driver": "local",
                "Mode": "z",
                "RW": True,
                "Propagation": "",
            },
            {
                "Type": "bind",
                "Name": "",
                "Source": "/host/dir",
                "Destination": "/mnt/host",
                "Mode": "ro",
                "RW": False,
                "Propagation": "rslave",
            },
        ],
    }


def _image_obj(tags=("nginx:latest",)):
    return Image(
        attrs={
            "Id": "sha256:9f1e2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e7",
            "RepoTags": list(tags),
            "RepoDigests": ["nginx@sha256:abcdef0123456789"],
            "Config": {"Labels": {"org.opencontainers.image.description": "x" * 500}},
            "Created": "2026-01-01T00:00:00Z",
            "Size": 4194304,
        }
    )


class _Container(Container):
    """Real Container whose ``.image`` is controlled (no client round-trip)."""

    def __init__(self, attrs, image=None):
        super().__init__(attrs)
        self._test_image = image

    @property
    def image(self):
        return self._test_image


def _rich_container():
    health = {
        "Status": "healthy",
        "FailingStreak": 0,
        "Log": [
            {"Start": "t1", "End": "e1", "ExitCode": 0, "Output": BIG_OUTPUT}
            for _ in range(5)
        ],
    }
    return _Container(_container_attrs(health=health), image=_image_obj())


# --- summary shape ----------------------------------------------------------


def test_container_summary_is_compact():
    s = docker_to_dict(_rich_container(), format="summary")
    assert set(s.keys()) == {
        "name",
        "short_id",
        "status",
        "state",
        "image",
        "mounts",
        "health",
    }
    assert s["name"] == "app"
    assert s["short_id"] == "f8fac9ce6323"
    assert s["status"] == "running"
    # image is a plain string reference, not a nested object
    assert s["image"] == "nginx:latest"
    # state is trimmed to exactly the six useful keys
    assert set(s["state"].keys()) == {
        "Status",
        "Running",
        "Restarting",
        "Dead",
        "ExitCode",
        "StartedAt",
    }
    assert s["state"]["Status"] == "running"
    # health has no Log
    assert "Log" not in s["health"]
    assert s["health"] == {"Status": "healthy", "FailingStreak": 0}
    # mounts trimmed to name + type
    assert s["mounts"] == [
        {"name": "app_data", "type": "volume"},
        {"name": "", "type": "bind"},
    ]
    # and it's small
    assert len(json.dumps(s)) < 1000


def test_container_summary_omits_health_when_absent():
    c = _Container(_container_attrs())
    s = docker_to_dict(c, format="summary")
    assert "health" not in s


def test_container_summary_image_ref_falls_back_to_first_tag():
    # Config.Image empty -> use first repo tag of the linked image
    attrs = _container_attrs(config_image="")
    c = _Container(attrs, image=_image_obj(tags=("myrepo/app:latest", "myrepo/app:v1")))
    s = docker_to_dict(c, format="summary")
    assert s["image"] == "myrepo/app:latest"


# --- full shape -------------------------------------------------------------


def test_container_full_keeps_inspect_shape_and_caps_log():
    c = _rich_container()
    f = docker_to_dict(c, format="full")
    assert f["id"] == c.id
    assert f["config"]["image"] == "nginx:latest"
    assert "Health" in f["state"]
    log = f["state"]["Health"]["Log"]
    assert len(log) == 5
    for entry in log:
        assert entry["Output"].endswith(TRUNCATION_MARK)
        assert len(entry["Output"]) <= HEALTH_LOG_ENTRY_CAP + len(TRUNCATION_MARK)
    # nested image present in full mode (sha256 short_id is 19 chars)
    assert f["image"]["short_id"] == "sha256:9f1e2b3c4d5e"
    # full is strictly larger than summary
    assert len(json.dumps(f)) > len(json.dumps(docker_to_dict(c, format="summary")))


def test_container_full_health_log_capped_even_when_shorter():
    # A short entry is returned unchanged (no marker).
    health = {
        "Status": "unhealthy",
        "FailingStreak": 2,
        "Log": [{"Start": "s", "End": "e", "ExitCode": 1, "Output": "ok\n"}],
    }
    c = _Container(_container_attrs(health=health), image=_image_obj())
    f = docker_to_dict(c, format="full")
    assert f["state"]["Health"]["Log"][0]["Output"] == "ok\n"


# --- health-log trimming helper --------------------------------------------


def test_trim_health_log_caps_each_entry():
    state = {
        "Status": "running",
        "Health": {"Status": "healthy", "Log": [{"Output": "y" * 3000}]},
    }
    trimmed = _trim_health_log(state)
    out = trimmed["Health"]["Log"][0]["Output"]
    assert len(out) == HEALTH_LOG_ENTRY_CAP + len(TRUNCATION_MARK)
    assert out.startswith("y" * HEALTH_LOG_ENTRY_CAP)


def test_trim_health_log_returns_nonhealth_state_untouched():
    state = {"Status": "exited"}
    assert _trim_health_log(state) is state
    assert _trim_health_log(None) is None


# --- images / volumes / networks -------------------------------------------


def test_image_summary_and_full():
    img = _image_obj()
    s = docker_to_dict(img, format="summary")
    assert set(s.keys()) == {"short_id", "tags", "size", "created"}
    assert s["tags"] == ["nginx:latest"]
    f = docker_to_dict(img, format="full")
    assert f["repo_digests"] == ["nginx@sha256:abcdef0123456789"]
    assert "labels" in f


def test_volume_summary_and_full():
    vol = Volume(
        attrs={
            "Id": "volid0000000000",
            "Name": "app_data",
            "Driver": "local",
            "Mountpoint": "/var/lib/docker/volumes/app_data/_data",
            "Labels": {"a": "b"},
            "CreatedAt": "2026-01-01T00:00:00Z",
            "Scope": "local",
        }
    )
    s = docker_to_dict(vol, format="summary")
    assert set(s.keys()) == {"name", "short_id", "driver", "mountpoint"}
    f = docker_to_dict(vol, format="full")
    assert f["labels"] == {"a": "b"}
    assert f["scope"] == "local"


def test_network_ignores_format():
    net = Network(
        attrs={
            "Id": "net0000000000",
            "Name": "bridge",
            "Driver": "bridge",
            "Scope": "local",
            "CreatedAt": "2026-01-01T00:00:00Z",
            "Labels": None,
        }
    )
    assert docker_to_dict(net, format="summary") == docker_to_dict(net, format="full")


# --- overrides -------------------------------------------------------------


def test_overrides_still_applied():
    s = docker_to_dict(
        _rich_container(), format="summary", overrides={"status": "removed"}
    )
    assert s["status"] == "removed"


# --- end-to-end through the MCP tool ---------------------------------------


class _FakeCollection:
    def __init__(self, items):
        self.items = items

    def list(self, **kwargs):
        return self.items


class _FakeClient:
    def __init__(self, containers):
        self.containers = _FakeCollection(containers)
        self.images = _FakeCollection([])
        self.networks = _FakeCollection([])
        self.volumes = _FakeCollection([])
        self.closed = False

    def close(self):
        self.closed = True


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_list_containers_default_is_summary(monkeypatch):
    fake = _FakeClient([_rich_container()])
    monkeypatch.setattr(server_module.docker, "from_env", lambda: fake)

    async with Client(app, raise_exceptions=True) as client:
        default = await client.call_tool("list_containers", {})
        full = await client.call_tool("list_containers", {"format": "full"})

    assert fake.closed

    rows = default.structured_content.get("result")
    assert isinstance(rows, list) and len(rows) == 1
    row = rows[0]
    # summary: image is a string, no config key, no Health.Log
    assert isinstance(row["image"], str)
    assert "config" not in row
    assert "Log" not in row["health"]
    assert row["health"]["Status"] == "healthy"

    full_rows = full.structured_content.get("result")
    full_row = full_rows[0]
    # full: inspect-level shape with capped health log
    assert full_row["config"]["image"] == "nginx:latest"
    assert "Health" in full_row["state"]
    assert full_row["state"]["Health"]["Log"][0]["Output"].endswith(TRUNCATION_MARK)

    # the compact default is a large fraction smaller than the full output
    assert len(json.dumps(full_rows)) > len(json.dumps(rows)) * 3


@pytest.mark.anyio
async def test_single_object_tools_default_to_full(monkeypatch):
    """run/stop/start keep returning the full object by default."""
    created: dict = {}

    class _RunCollection:
        def run(self, **kwargs):
            created.update(kwargs)
            return _rich_container()

    class _Client:
        def __init__(self):
            self.containers = _RunCollection()
            self.images = _FakeCollection([])
            self.networks = _FakeCollection([])
            self.volumes = _FakeCollection([])
            self.closed = False

        def close(self):
            self.closed = True

    fake = _Client()
    monkeypatch.setattr(server_module.docker, "from_env", lambda: fake)

    async with Client(app, raise_exceptions=True) as client:
        res = await client.call_tool("run_container", {"image": "nginx"})

    row = res.structured_content
    assert row["config"]["image"] == "nginx:latest"
    assert "Health" in row["state"]
