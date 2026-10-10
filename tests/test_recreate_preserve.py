from __future__ import annotations

from unittest.mock import Mock, patch

import pytest
from docker.errors import ImageNotFound, NotFound
from docker.models.containers import Container

import mcp_server_docker.server as server_module


def _container_attrs(*, running: bool = True) -> dict:
    return {
        "Image": "sha256:old-image",
        "Config": {
            "Hostname": "abc123def456",
            "Domainname": "",
            "User": "1000:1000",
            "Env": ["MODE=test"],
            "Cmd": ["serve"],
            "Healthcheck": {"Test": ["CMD", "healthcheck"], "Interval": 30_000_000_000},
            "Image": "example:old",
            "Labels": {"app": "example", "com.docker.compose.project": "test-project"},
            "ExposedPorts": {"8080/tcp": {}},
            "Volumes": {"/cache": {}},
            "WorkingDir": "/app",
            "Entrypoint": ["/entrypoint"],
        },
        "HostConfig": {
            "NetworkMode": "prod",
            "Binds": ["/srv/example:/data:ro"],
            "Mounts": [],
            "PortBindings": {
                "8080/tcp": [{"HostIp": "127.0.0.1", "HostPort": "18080"}]
            },
            "RestartPolicy": {"Name": "unless-stopped", "MaximumRetryCount": 0},
            "AutoRemove": False,
        },
        "Mounts": [
            {
                "Type": "bind",
                "Source": "/srv/example",
                "Destination": "/data",
                "RW": False,
            },
            {
                "Type": "volume",
                "Name": "example-cache-volume",
                "Destination": "/cache",
                "RW": True,
            },
        ],
        "NetworkSettings": {
            "Networks": {
                "prod": {
                    "IPAMConfig": {"IPv4Address": "172.20.0.25"},
                    "Aliases": ["example", "example-service"],
                    "NetworkID": "runtime-network-id",
                    "EndpointID": "runtime-endpoint-id",
                    "IPAddress": "172.20.0.25",
                }
            }
        },
        "State": {"Running": running, "Status": "running" if running else "exited"},
    }


def _mock_container(*, running: bool = True) -> Mock:
    container = Mock(spec=Container)
    container.id = "abc123def4567890"
    container.name = "example-container"
    container.attrs = _container_attrs(running=running)
    return container


@patch("mcp_server_docker.server.docker_to_dict", return_value={"status": "recreated"})
def test_opt_in_recreate_preserves_original_configuration_and_running_state(converter):
    existing = _mock_container(running=True)
    replacement = _mock_container(running=True)
    replacement.id = "new-container-id"
    client = Mock()
    client.containers.get.side_effect = [existing, replacement]
    client.api.create_container_from_config.return_value = {"Id": replacement.id}
    with patch.object(server_module, "_client", return_value=client):
        result = server_module.recreate_container(
            None, image="example:new", name="example-container", preserve_existing=True
        )
    assert result["status"] == "recreated"
    client.images.get.assert_called_once_with("example:new")
    first = client.api.create_container_from_config.call_args.args[0]
    assert first["Env"] == ["MODE=test"]
    assert first["HostConfig"]["RestartPolicy"]["Name"] == "unless-stopped"
    assert "example-cache-volume:/cache:rw" in first["HostConfig"]["Binds"]
    assert first["NetworkingConfig"]["EndpointsConfig"]["prod"]["Aliases"] == [
        "example",
        "example-service",
    ]
    existing.stop.assert_called_once()
    existing.remove.assert_called_once()
    replacement.start.assert_called_once()


def test_opt_in_recreate_missing_image_does_not_stop_original():
    existing = _mock_container(running=True)
    client = Mock()
    client.containers.get.return_value = existing
    client.images.get.side_effect = ImageNotFound("missing")
    with (
        patch.object(server_module, "_client", return_value=client),
        pytest.raises(ImageNotFound),
    ):
        server_module.recreate_container(
            None,
            image="missing:image",
            name="example-container",
            preserve_existing=True,
        )
    existing.stop.assert_not_called()
    existing.remove.assert_not_called()


def test_opt_in_recreate_replaces_failed_creation_with_original_config():
    existing = _mock_container(running=True)
    restored = _mock_container(running=True)
    restored.id = "restored-container-id"
    client = Mock()
    client.containers.get.side_effect = [existing, restored]
    client.api.create_container_from_config.side_effect = [
        RuntimeError("replacement rejected"),
        {"Id": restored.id},
    ]
    with (
        patch.object(server_module, "_client", return_value=client),
        pytest.raises(RuntimeError, match="restored successfully"),
    ):
        server_module.recreate_container(
            None, image="example:new", name="example-container", preserve_existing=True
        )
    assert client.api.create_container_from_config.call_count == 2
    original_payload = client.api.create_container_from_config.call_args_list[1].args[0]
    assert original_payload["Image"] == "sha256:old-image"
    restored.start.assert_called_once()


def test_default_recreate_keeps_legacy_arguments_and_behavior():
    original = _mock_container()
    new = _mock_container()
    client = Mock()
    client.containers.get.return_value = original
    client.containers.run.return_value = new
    with (
        patch.object(server_module, "_client", return_value=client),
        patch.object(server_module, "docker_to_dict", return_value={"id": "new"}),
    ):
        result = server_module.recreate_container(
            None,
            image="example:new",
            name="example-container",
            detach=True,
            preserve_existing=False,
            environment={"X": "1"},
        )
    assert result == {"id": "new"}
    original.stop.assert_called_once()
    original.remove.assert_called_once()
    client.containers.run.assert_called_once()
    assert client.containers.run.call_args.kwargs["environment"] == {"X": "1"}
    client.api.create_container_from_config.assert_not_called()


def test_opt_in_recreate_rejects_overrides_before_inspection_or_mutation():
    client = Mock()
    with (
        patch.object(server_module, "_client", return_value=client),
        pytest.raises(ValueError, match="cannot be combined"),
    ):
        server_module.recreate_container(
            None,
            image="example:new",
            name="example-container",
            preserve_existing=True,
            environment={"UNSAFE": "override"},
        )
    client.containers.get.assert_not_called()
    client.containers.run.assert_not_called()
    client.api.create_container_from_config.assert_not_called()


def test_opt_in_recreate_can_preserve_stopped_container():
    original = _mock_container(running=False)
    replacement = _mock_container(running=False)
    replacement.id = "new-stopped-container"
    client = Mock()
    client.containers.get.side_effect = [original, replacement]
    client.api.create_container_from_config.return_value = {"Id": replacement.id}
    with (
        patch.object(server_module, "_client", return_value=client),
        patch.object(
            server_module, "docker_to_dict", return_value={"id": replacement.id}
        ),
    ):
        server_module.recreate_container(
            None,
            image="example:new",
            container_id="example-container",
            preserve_existing=True,
        )
    original.stop.assert_not_called()
    replacement.start.assert_not_called()
    original.remove.assert_called_once_with()


def test_opt_in_recreate_fails_closed_if_rollback_also_fails():
    original = _mock_container(running=True)
    client = Mock()
    client.containers.get.return_value = original
    client.api.create_container_from_config.side_effect = [
        RuntimeError("create failed"),
        RuntimeError("rollback failed"),
    ]
    with (
        patch.object(server_module, "_client", return_value=client),
        pytest.raises(RuntimeError, match="automatic restoration also failed"),
    ):
        server_module.recreate_container(
            None,
            image="example:new",
            name="example-container",
            preserve_existing=True,
        )
    assert client.api.create_container_from_config.call_count == 2


def test_opt_in_recreate_auto_remove_race_still_creates_replacement():
    original = _mock_container(running=True)
    original.attrs["HostConfig"]["AutoRemove"] = True
    original.remove.side_effect = NotFound("already removed")
    replacement = _mock_container(running=True)
    replacement.id = "new-auto-remove-container"
    client = Mock()
    client.containers.get.side_effect = [original, replacement]
    client.api.create_container_from_config.return_value = {"Id": replacement.id}
    with (
        patch.object(server_module, "_client", return_value=client),
        patch.object(
            server_module, "docker_to_dict", return_value={"id": replacement.id}
        ),
    ):
        server_module.recreate_container(
            None,
            image="example:new",
            name="example-container",
            preserve_existing=True,
        )
    replacement.start.assert_called_once_with()


def test_opt_in_recreate_does_not_reuse_mount_target_twice():
    original = _mock_container()
    original.attrs["HostConfig"]["Mounts"] = [
        {"Type": "volume", "Source": "example-cache-volume", "Target": "/cache"}
    ]
    payload = server_module._build_recreate_payload(original, "example:new")
    assert "example-cache-volume:/cache:rw" not in payload["HostConfig"]["Binds"]
