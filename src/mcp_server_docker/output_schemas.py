from typing import Any

from docker.models.containers import Container
from docker.models.images import Image
from docker.models.networks import Network
from docker.models.volumes import Volume


def docker_to_dict(
    obj: Image | Container | Volume | Network, overrides: dict[str, Any] | None = None
) -> dict[str, Any]:
    result = None

    if isinstance(obj, Image):
        result = {
            "id": obj.id,
            "tags": obj.tags,
            "short_id": obj.short_id,
            "labels": obj.labels,
            "repo_tags": obj.attrs.get("RepoTags"),
            "repo_digests": obj.attrs.get("RepoDigests"),
            "created": obj.attrs.get("Created"),
            "size": obj.attrs.get("Size"),
        }

    if isinstance(obj, Container):
        config: dict[str, Any] = obj.attrs.get("Config", {})

        result = {
            "id": obj.id,
            "name": obj.name,
            "short_id": obj.short_id,
            "image": docker_to_dict(obj.image) if obj.image is not None else None,
            "status": obj.status,
            "labels": obj.labels,
            "ports": obj.ports,
            "created": obj.attrs.get("Created"),
            "state": obj.attrs.get("State"),
            "restart_count": obj.attrs.get("RestartCount"),
            "networks": list(
                obj.attrs.get("NetworkSettings", {}).get("Networks", {}).keys()
            ),
            "mounts": obj.attrs.get("Mounts"),
            "config": {
                "hostname": config.get("Hostname"),
                "user": config.get("User"),
                "image": config.get("Image"),
                # It's common for Docker containers to have secrets configured as
                # plaintext env vars, so we only inform the LLM of the keys.
                # It's unclear how best to share env values with the LLM without
                # risking exposure. A few approaches that come to mind:
                #
                #    - Naive: redact values with keys containing "password" or "key"
                #    - Advanced: use a tool like detect-secrets for detection: https://github.com/Yelp/detect-secrets
                #    - Manual: require users to explicitly mark some env vars as secrets with MCP server configuration
                #
                # Perhaps some combination of these would be best. In any case,
                # users of this MCP server should have to opt-in to this behavior since
                # it poses a security risk no matter what.
                "env_keys": config.get("Env", {}).keys(),
            },
        }

    if isinstance(obj, Network):
        result = {
            "id": obj.id,
            "name": obj.name,
            "short_id": obj.short_id,
            "driver": obj.attrs.get("Driver"),
            "scope": obj.attrs.get("Scope"),
            "created": obj.attrs.get("CreatedAt"),
            "labels": obj.attrs.get("Labels"),
        }

    if isinstance(obj, Volume):
        result = {
            "id": obj.id,
            "name": obj.name,
            "short_id": obj.short_id,
            "labels": obj.attrs.get("Labels", {}),
            "mountpoint": obj.attrs.get("Mountpoint"),
            "created": obj.attrs.get("CreatedAt"),
            "driver": obj.attrs.get("Driver"),
            "scope": obj.attrs.get("Scope"),
        }

    if result is None:
        raise ValueError(f"Unsupported object type: {type(obj)}")

    return result if overrides is None else {**result, **overrides}
