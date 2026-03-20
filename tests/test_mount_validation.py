"""Tests for blocked volume mount and build context validation."""

import pytest
from pydantic import ValidationError

from mcp_server_docker.input_schemas import (
    BLOCKED_MOUNT_SOURCES,
    BuildImageInput,
    CreateContainerInput,
)


class TestBlockedVolumeMounts:
    """Verify that dangerous host paths are rejected as volume sources."""

    @pytest.mark.parametrize("blocked_path", sorted(BLOCKED_MOUNT_SOURCES))
    def test_dict_format_blocks_dangerous_paths(self, blocked_path: str) -> None:
        with pytest.raises(ValidationError, match="blocked"):
            CreateContainerInput(
                image="alpine:latest",
                volumes={blocked_path: {"bind": "/mnt", "mode": "ro"}},
            )

    @pytest.mark.parametrize("blocked_path", sorted(BLOCKED_MOUNT_SOURCES))
    def test_list_format_blocks_dangerous_paths(self, blocked_path: str) -> None:
        with pytest.raises(ValidationError, match="blocked"):
            CreateContainerInput(
                image="alpine:latest",
                volumes=[f"{blocked_path}:/mnt:ro"],
            )

    def test_docker_socket_blocked(self) -> None:
        with pytest.raises(ValidationError, match="container escape"):
            CreateContainerInput(
                image="alpine:latest",
                volumes={
                    "/var/run/docker.sock": {
                        "bind": "/var/run/docker.sock",
                        "mode": "rw",
                    }
                },
            )

    def test_subpath_of_blocked_path_also_blocked(self) -> None:
        with pytest.raises(ValidationError, match="blocked"):
            CreateContainerInput(
                image="alpine:latest",
                volumes=["/proc/1/root:/mnt:ro"],
            )

    def test_safe_volume_mount_allowed(self) -> None:
        container = CreateContainerInput(
            image="nginx:latest",
            volumes={"/home/user/app": {"bind": "/app", "mode": "ro"}},
        )
        assert container.volumes is not None

    def test_safe_list_volume_mount_allowed(self) -> None:
        container = CreateContainerInput(
            image="nginx:latest",
            volumes=["/tmp/data:/data:rw"],
        )
        assert container.volumes is not None

    def test_none_volumes_allowed(self) -> None:
        container = CreateContainerInput(image="alpine:latest")
        assert container.volumes is None

    def test_multiple_volumes_one_blocked(self) -> None:
        with pytest.raises(ValidationError, match="blocked"):
            CreateContainerInput(
                image="alpine:latest",
                volumes={
                    "/tmp/safe": {"bind": "/safe", "mode": "ro"},
                    "/var/run/docker.sock": {
                        "bind": "/var/run/docker.sock",
                        "mode": "rw",
                    },
                },
            )


class TestBlockedBuildContextPaths:
    """Verify that sensitive paths are rejected as Docker build contexts."""

    @pytest.mark.parametrize("blocked_path", sorted(BLOCKED_MOUNT_SOURCES))
    def test_blocked_paths_rejected_as_build_context(self, blocked_path: str) -> None:
        with pytest.raises(ValidationError, match="blocked"):
            BuildImageInput(path=blocked_path, tag="test:latest")

    def test_safe_build_context_allowed(self) -> None:
        build = BuildImageInput(path="/home/user/project", tag="myapp:latest")
        assert build.path == "/home/user/project"

    def test_subpath_of_blocked_path_rejected(self) -> None:
        with pytest.raises(ValidationError, match="blocked"):
            BuildImageInput(path="/proc/self", tag="test:latest")
