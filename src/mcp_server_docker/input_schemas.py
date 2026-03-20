import json
import os
from datetime import datetime
from typing import Any, Literal, get_args, get_origin

from pydantic import (
    BaseModel,
    Field,
    ValidationInfo,
    computed_field,
    field_validator,
    model_validator,
)

# Host paths that must never be bind-mounted into containers.
# Mounting container runtime sockets allows full host takeover from within
# a container. /proc, /sys, and /dev expose kernel interfaces that enable
# privilege escalation. These restrictions prevent container escape attacks
# triggered via prompt injection or untrusted LLM tool calls.
BLOCKED_MOUNT_SOURCES = frozenset(
    {
        "/var/run/docker.sock",
        "/run/docker.sock",
        "/var/run/containerd",
        "/run/containerd",
        "/var/run/crio",
        "/run/crio",
        "/proc",
        "/sys",
        "/dev",
    }
)


def _extract_host_paths(
    volumes: dict[str, dict[str, str]] | list[str],
) -> list[str]:
    """Return host source paths from a Docker volume mount specification.

    Handles both formats accepted by the Docker SDK:
      dict: {"/host/path": {"bind": "/container/path", "mode": "rw"}}
      list: ["/host/path:/container/path:rw"]
    """
    if isinstance(volumes, dict):
        return list(volumes.keys())

    paths: list[str] = []
    for entry in volumes:
        # list entries are "source:dest[:mode]"
        parts = entry.split(":")
        if parts:
            paths.append(parts[0])
    return paths


def _is_blocked_mount(host_path: str) -> str | None:
    """Return the matched blocked path if host_path resolves to one, else None.

    Resolves symlinks so that aliased paths (e.g. /tmp/sock pointing at
    /var/run/docker.sock) are still caught.
    """
    try:
        resolved = os.path.realpath(host_path)
    except (OSError, ValueError):
        resolved = host_path

    for blocked in BLOCKED_MOUNT_SOURCES:
        if resolved == blocked or resolved.startswith(blocked + os.sep):
            return blocked
    return None


class JSONParsingModel(BaseModel):
    """
    A base Pydantic model that attempts to parse JSON strings for non-primitive fields.
    If a string is provided for a field that expects a complex type (dict, list, or another model),
    it will attempt to parse it as JSON.

    Claude appears to not understand that a nested field shouldn't be a JSON-encoded string...
    But it does send valid JSON!
    """

    @field_validator("*", mode="before")
    @classmethod
    def _try_parse_json(cls, value: Any, info: ValidationInfo):
        if not isinstance(value, str):
            return value

        fields = cls.model_fields
        field_name = info.field_name

        if field_name not in fields:
            return value

        field = fields[field_name]
        field_type = field.annotation

        # Handle Optional/Union types
        origin = get_origin(field_type)
        if origin is not None:
            args = get_args(field_type)
            # Find the non-None type in case of Optional
            field_type = next(
                (arg for arg in args if arg is not type(None)), field_type
            )

        # Don't try to parse strings, numbers, or dates
        if field_type in (str, int, float, bool, datetime):
            return value

        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value


class FetchContainerLogsInput(JSONParsingModel):
    container_id: str = Field(..., description="Container ID or name")
    tail: int | Literal["all"] = Field(
        100, description="Number of lines to show from the end"
    )


class ListContainersFilters(JSONParsingModel):
    label: list[str] | None = Field(
        None, description="Filter by label, either `key` or `key=value` format"
    )


class ListContainersInput(JSONParsingModel):
    all: bool = Field(
        False, description="Show all containers (default shows just running)"
    )
    filters: ListContainersFilters | None = Field(None, description="Filter containers")


class CreateContainerInput(JSONParsingModel):
    """
    Schema for creating a new container.

    This is passed to the Python Docker SDK directly, so the fields are the same
    as the `docker.containers.create` method.
    """

    detach: bool = Field(
        True,
        description="Run container in the background. Should be True for long-running containers, can be false for short-lived containers",
    )
    image: str = Field(..., description="Docker image name")
    name: str | None = Field(None, description="Container name")
    entrypoint: str | None = Field(None, description="Entrypoint to run in container")
    command: str | None = Field(None, description="Command to run in container")
    network: str | None = Field(None, description="Network to attach the container to")
    environment: dict[str, str] | None = Field(
        None, description="Environment variables dictionary"
    )
    ports: dict[str, int | list[int] | tuple[str, int] | None] | None = Field(
        None,
        description="A map whose keys are the container port, and the values are the host port(s) to bind to.",
    )
    volumes: dict[str, dict[str, str]] | list[str] | None = Field(
        None, description="Volume mappings"
    )

    @field_validator("volumes", mode="after")
    @classmethod
    def _reject_blocked_mounts(
        cls, volumes: dict[str, dict[str, str]] | list[str] | None
    ) -> dict[str, dict[str, str]] | list[str] | None:
        if volumes is None:
            return volumes

        for host_path in _extract_host_paths(volumes):
            blocked = _is_blocked_mount(host_path)
            if blocked is not None:
                raise ValueError(
                    f"Volume mount source '{host_path}' is blocked. "
                    f"Mounting '{blocked}' exposes host resources that "
                    f"allow container escape or privilege escalation."
                )
        return volumes

    labels: dict[str, str] | list[str] | None = Field(
        None,
        description="Container labels, either as a dictionary or a list of key=value strings",
    )
    auto_remove: bool = Field(False, description="Automatically remove the container")


class RecreateContainerInput(CreateContainerInput):
    container_id: str | None = Field(
        None,
        description="Container ID to recreate. The `name` parameter will be used if this is not provided",
    )

    @computed_field
    @property
    def resolved_container_id(self) -> str:
        return self.container_id or self.name  # pyright: ignore

    @model_validator(mode="after")
    def validate_container_id(self):
        if self.container_id is None and self.name is None:
            raise ValueError(
                "container_id or name is required for identifying the container to stop+remove"
            )
        return self


class ContainerActionInput(JSONParsingModel):
    container_id: str = Field(..., description="Container ID or name")


class RemoveContainerInput(JSONParsingModel):
    container_id: str = Field(..., description="Container ID or name")
    force: bool = Field(False, description="Force remove the container")


class ListImagesFilters(JSONParsingModel):
    dangling: bool | None = Field(None, description="Show dangling images")
    label: list[str] | None = Field(
        None, description="Filter by label, either `key` or `key=value` format"
    )


class ListImagesInput(JSONParsingModel):
    name: str | None = Field(
        None, description="Filter images by repository name, if desired"
    )
    all: bool = Field(False, description="Show all images (default hides intermediate)")
    filters: ListImagesFilters | None = Field(None, description="Filter images")


class PullPushImageInput(JSONParsingModel):
    repository: str = Field(..., description="Image repository")
    tag: str | None = Field("latest", description="Image tag")


class BuildImageInput(JSONParsingModel):
    path: str = Field(..., description="Path to build context")
    tag: str = Field(..., description="Image tag")
    dockerfile: str | None = Field(None, description="Path to Dockerfile")

    @field_validator("path", mode="after")
    @classmethod
    def _reject_sensitive_build_context(cls, path: str) -> str:
        try:
            resolved = os.path.realpath(path)
        except (OSError, ValueError):
            resolved = path

        for blocked in BLOCKED_MOUNT_SOURCES:
            if resolved == blocked or resolved.startswith(blocked + os.sep):
                raise ValueError(
                    f"Build context path '{path}' is blocked. "
                    f"'{blocked}' contains sensitive host resources that "
                    f"would be sent to the Docker daemon as build context."
                )
        return path


class RemoveImageInput(JSONParsingModel):
    image: str = Field(..., description="Image ID or name")
    force: bool = Field(False, description="Force remove the image")


class ListNetworksFilter(JSONParsingModel):
    label: list[str] | None = Field(
        None, description="Filter by label, either `key` or `key=value` format"
    )


class ListNetworksInput(JSONParsingModel):
    filters: ListNetworksFilter | None = Field(None, description="Filter networks")


class CreateNetworkInput(JSONParsingModel):
    name: str = Field(..., description="Network name")
    driver: str | None = Field("bridge", description="Network driver")
    internal: bool = Field(False, description="Create an internal network")
    labels: dict[str, str] | None = Field(None, description="Network labels")


class RemoveNetworkInput(JSONParsingModel):
    network_id: str = Field(..., description="Network ID or name")


class ListVolumesInput(JSONParsingModel):
    pass


class CreateVolumeInput(JSONParsingModel):
    name: str = Field(..., description="Volume name")
    driver: str | None = Field("local", description="Volume driver")
    labels: dict[str, str] | None = Field(None, description="Volume labels")


class RemoveVolumeInput(JSONParsingModel):
    volume_name: str = Field(..., description="Volume name")
    force: bool = Field(False, description="Force remove the volume")


class DockerComposePromptInput(BaseModel):
    name: str
    containers: str
