from typing import Any, Literal

from docker.models.containers import Container
from docker.models.images import Image
from docker.models.networks import Network
from docker.models.volumes import Volume

# Docker retains up to 5 healthcheck log entries and never bounds their size.
# A healthcheck that `curl`s a web page (or dumps a JSON status payload)
# therefore balloons every container listing by tens of KB each. Cap each
# entry's output so a `list_containers` call stays small no matter what the
# healthcheck prints.
HEALTH_LOG_ENTRY_CAP = 500

# The subset of a container's inspect State that a listing actually needs.
_CONTAINER_STATE_KEYS = (
    "Status",
    "Running",
    "Restarting",
    "Dead",
    "ExitCode",
    "StartedAt",
)


def _cap_text(value: Any, limit: int = HEALTH_LOG_ENTRY_CAP) -> Any:
    """Truncate a string to ``limit`` characters, tagging the cut.

    Non-string values (or short strings) are returned unchanged.
    """
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + "… [truncated]"
    return value


def _trim_health_log(state: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return ``State`` with each ``Health.Log[*].Output`` entry capped.

    Returns the original object untouched when there is no health log, so
    non-health-checked containers pay nothing for this.
    """
    if not isinstance(state, dict):
        return state
    health = state.get("Health")
    if not isinstance(health, dict) or not isinstance(health.get("Log"), list):
        return state
    log = [
        {**entry, "Output": _cap_text(entry.get("Output"))}
        if isinstance(entry, dict)
        else entry
        for entry in health["Log"]
    ]
    return {**state, "Health": {**health, "Log": log}}


def _container_image_ref(obj: Container) -> str | None:
    """Cheap image reference for a listing.

    Prefers ``Config.Image`` (no extra daemon round-trip). Falls back to the
    first repo tag of the linked image when ``Config.Image`` is empty.
    """
    config: dict[str, Any] = obj.attrs.get("Config") or {}
    ref = config.get("Image")
    if ref:
        return ref
    image = obj.image
    if image is not None:
        tags = getattr(image, "tags", None) or []
        if tags:
            return tags[0]
    return None


def _container_summary(obj: Container) -> dict[str, Any]:
    """Compact per-container row: the ``docker ps`` columns plus health."""
    state: dict[str, Any] = obj.attrs.get("State") or {}
    result: dict[str, Any] = {
        "name": obj.name,
        "short_id": obj.short_id,
        "status": obj.status,
        "state": {key: state.get(key) for key in _CONTAINER_STATE_KEYS},
        "image": _container_image_ref(obj),
        "mounts": [
            {"name": mount.get("Name"), "type": mount.get("Type")}
            for mount in (obj.attrs.get("Mounts") or [])
        ],
    }
    health = state.get("Health")
    if isinstance(health, dict):
        result["health"] = {
            "Status": health.get("Status"),
            "FailingStreak": health.get("FailingStreak"),
        }
    return result


def _container_full(obj: Container) -> dict[str, Any]:
    """Inspect-level container shape (pre-existing keys), health log capped."""
    config: dict[str, Any] = obj.attrs.get("Config") or {}
    return {
        "id": obj.id,
        "name": obj.name,
        "short_id": obj.short_id,
        "image": docker_to_dict(obj.image) if obj.image else None,
        "status": obj.status,
        "labels": config.get("Labels", {}),
        "ports": obj.ports,
        "created": obj.attrs.get("Created"),
        "state": _trim_health_log(obj.attrs.get("State")),
        "restart_count": obj.attrs.get("RestartCount"),
        "networks": list(
            obj.attrs.get("NetworkSettings", {}).get("Networks", {}).keys()
        ),
        "mounts": obj.attrs.get("Mounts"),
        "config": {
            "hostname": config.get("Hostname"),
            "user": config.get("User"),
            "image": config.get("Image"),
        },
    }


def _image_summary(obj: Image) -> dict[str, Any]:
    return {
        "short_id": obj.short_id,
        "tags": obj.tags,
        "size": obj.attrs.get("Size"),
        "created": obj.attrs.get("Created"),
    }


def _image_full(obj: Image) -> dict[str, Any]:
    img_config: dict[str, Any] = obj.attrs.get("Config") or {}
    return {
        "id": obj.id,
        "tags": obj.tags,
        "short_id": obj.short_id,
        "labels": img_config.get("Labels", {}),
        "repo_tags": obj.attrs.get("RepoTags"),
        "repo_digests": obj.attrs.get("RepoDigests"),
        "created": obj.attrs.get("Created"),
        "size": obj.attrs.get("Size"),
    }


def _volume_summary(obj: Volume) -> dict[str, Any]:
    return {
        "name": obj.name,
        "short_id": obj.short_id,
        "driver": obj.attrs.get("Driver"),
        "mountpoint": obj.attrs.get("Mountpoint"),
    }


def _volume_full(obj: Volume) -> dict[str, Any]:
    return {
        "id": obj.id,
        "name": obj.name,
        "short_id": obj.short_id,
        "labels": obj.attrs.get("Labels", {}),
        "mountpoint": obj.attrs.get("Mountpoint"),
        "created": obj.attrs.get("CreatedAt"),
        "driver": obj.attrs.get("Driver"),
        "scope": obj.attrs.get("Scope"),
    }


def _network(obj: Network) -> dict[str, Any]:
    return {
        "id": obj.id,
        "name": obj.name,
        "short_id": obj.short_id,
        "driver": obj.attrs.get("Driver"),
        "scope": obj.attrs.get("Scope"),
        "created": obj.attrs.get("CreatedAt"),
        "labels": obj.attrs.get("Labels"),
    }


def docker_to_dict(
    obj: Image | Container | Volume | Network,
    overrides: dict[str, Any] | None = None,
    format: Literal["summary", "full"] = "full",
) -> dict[str, Any]:
    """Serialize a docker-py model to a plain dict.

    ``format`` selects the verbosity:

    * ``"summary"`` — a compact row tuned for listing (drops ``Health.Log``,
      the nested image object, labels and digests). Use this for the
      ``list_*`` tools so a listing of N containers stays small.
    * ``"full"`` — the inspect-level shape used by the single-object tools
      (``run_container``, ``stop_container``, …). Health log entries are
      still capped to :data:`HEALTH_LOG_ENTRY_CAP` characters.

    ``format`` has no effect on networks (they are already lean).
    """
    result = None

    if isinstance(obj, Container):
        result = (
            _container_summary(obj) if format == "summary" else _container_full(obj)
        )

    if isinstance(obj, Image):
        result = _image_summary(obj) if format == "summary" else _image_full(obj)

    if isinstance(obj, Volume):
        result = _volume_summary(obj) if format == "summary" else _volume_full(obj)

    if isinstance(obj, Network):
        result = _network(obj)

    if result is None:
        raise ValueError(f"Unsupported object type: {type(obj)}")

    return result if overrides is None else {**result, **overrides}
