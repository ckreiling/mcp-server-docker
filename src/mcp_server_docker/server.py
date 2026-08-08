"""MCPServer v2 implementation for Docker."""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Annotated, Any, Literal

import docker
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from mcp_server_docker._version import __version__
from mcp_server_docker.output_schemas import docker_to_dict


@dataclass
class AppContext:
    """State made available to handlers for one running server."""

    docker: docker.DockerClient


class ListContainersFilters(BaseModel):
    label: list[str] | None = Field(
        None, description="Filter by label, either `key` or `key=value` format"
    )


class ListImagesFilters(BaseModel):
    dangling: bool | None = Field(None, description="Show dangling images")
    label: list[str] | None = Field(
        None, description="Filter by label, either `key` or `key=value` format"
    )


class ListNetworksFilter(BaseModel):
    label: list[str] | None = Field(
        None, description="Filter by label, either `key` or `key=value` format"
    )


ContainerID = Annotated[str, Field(description="Container ID or name")]
ImageName = Annotated[str, Field(description="Docker image name")]
Detach = Annotated[bool, Field(description="Run container in the background")]
Entrypoint = Annotated[str | None, Field(description="Entrypoint to run in container")]
ContainerCommand = Annotated[
    str | None, Field(description="Command to run in container")
]
NetworkName = Annotated[
    str | None, Field(description="Network to attach the container to")
]
Environment = Annotated[
    dict[str, str] | None, Field(description="Environment variables dictionary")
]
PortBindings = Annotated[
    dict[str, int | list[int] | tuple[str, int] | None] | None,
    Field(description="Container-to-host port bindings"),
]
VolumeMappings = Annotated[
    dict[str, dict[str, str]] | list[str] | None, Field(description="Volume mappings")
]
ContainerLabels = Annotated[
    dict[str, str] | list[str] | None, Field(description="Container labels")
]
AutoRemove = Annotated[bool, Field(description="Automatically remove the container")]


def _client(ctx: Context[AppContext]) -> docker.DockerClient:
    return ctx.request_context.lifespan_context.docker


@asynccontextmanager
async def lifespan(_: MCPServer[AppContext]) -> AsyncIterator[AppContext]:
    """Create and close the Docker client for one server lifetime."""
    client = docker.from_env()
    try:
        yield AppContext(docker=client)
    finally:
        client.close()


app = MCPServer("docker-server", version=__version__, lifespan=lifespan)


@app.prompt(
    name="docker_compose", description="Treat the LLM like a Docker Compose manager"
)
def docker_compose(ctx: Context, name: str, containers: str) -> str:
    client = ctx.request_context.lifespan_context.docker
    project_label = f"mcp-server-docker.project={name}"
    existing_containers = client.containers.list(filters={"label": project_label})
    volumes = client.volumes.list(filters={"label": project_label})
    networks = client.networks.list(filters={"label": project_label})
    return f"""
You are going to act as a Docker Compose manager, using the Docker Tools
available to you. Instead of being provided a `docker-compose.yml` file,
you will be given instructions in plain language, and interact with the
user through a plan+apply loop, akin to how Terraform operates.

Every Docker resource you create must be assigned the following label:

{project_label}

You should use this label to filter resources when possible.

Every Docker resource you create must also be prefixed with the project name, followed by a dash (`-`):

{name}-{{ResourceName}}

Here are the resources currently present in the project, based on the presence of the above label:

<BEGIN CONTAINERS>
{json.dumps([docker_to_dict(c) for c in existing_containers], indent=2)}
<END CONTAINERS>
<BEGIN VOLUMES>
{json.dumps([docker_to_dict(v) for v in volumes], indent=2)}
<END VOLUMES>
<BEGIN NETWORKS>
{json.dumps([docker_to_dict(n) for n in networks], indent=2)}
<END NETWORKS>

Do not retry the same failed action more than once. Prefer terminating your output
when presented with 3 errors in a row, and ask a clarifying question to
form better inputs or address the error.

For container images, always prefer using the `latest` image tag, unless the user specifies a tag specifically.
So if a user asks to deploy Nginx, you should pull `nginx:latest`.

Below is a description of the state of the Docker resources which the user would like you to manage:

<BEGIN DOCKER-RESOURCES>
{containers}
<END DOCKER-RESOURCES>

Respond to this message with a plan of what you will do, in the EXACT format below:

<BEGIN FORMAT>
## Introduction

I will be assisting with deploying Docker containers for project: `{name}`.

### Plan+Apply Loop

I will run in a plan+apply loop when you request changes to the project. This is
to ensure that you are aware of the changes I am about to make, and to give you
the opportunity to ask questions or make tweaks.

Instruct me to apply immediately (without confirming the plan with you) when you desire to do so.

## Commands

Instruct me with the following commands at any point:

- `help`: print this list of commands
- `apply`: apply a given plan
- `down`: stop containers in the project
- `ps`: list containers in the project
- `quiet`: turn on quiet mode (default)
- `verbose`: turn on verbose mode (I will explain a lot!)
- `destroy`: produce a plan to destroy all resources in the project

## Plan

I plan to take the following actions:

1. CREATE ...
2. READ ...
3. UPDATE ...
4. DESTROY ...
5. RECREATE ...
...
N. ...

Respond `apply` to apply this plan. Otherwise, provide feedback and I will present you with an updated plan.
<END FORMAT>

Always apply a plan in dependency order. For example, if you are creating a container that depends on a
database, create the database first, and abort the apply if dependency creation fails. Likewise, 
destruction should occur in the reverse dependency order, and be aborted if destroying a particular resource fails.

Plans should only create, update, or destroy resources in the project. Relatedly, "recreate" should
be used to indicate a destroy followed by a create; always prefer udpating a resource when possible,
only recreating it if required (e.g. for immutable resources like containers).

If the project already exists (as indicated by the presence of resources above) and your plan would
produce no changes, simply respond with "No changes to make; project is up-to-date." If the user requests
changes that would render a resource obsolete (e.g. an unused volume), you should destroy the resource.

If you produce a plan and the next user message is not `apply`, simply drop the plan and inform
the user that they must explicitly include "apply" in the message. Only
apply a plan if it is contained in your latest message, otherwise ask the user to provide
their desires for the new plan.

IMPORTANT: maintain brevvity throughout your responses, unless instructed to be verbose.

The following are guidelines for you to follow when interacting with Docker Tools:

- Always prefer `run_container` for starting a container, instead of `create_container`+`start_container`.
- Always prefer `recreate_container` for updating a container, instead of `stop_container`+`remove_container`+`run_container`.
"""


@app.resource(
    "docker://containers/{container_id}/logs",
    name="Container logs",
    description="Live logs for a container",
    mime_type="text/plain",
)
def container_logs(container_id: str, ctx: Context) -> str:
    container = ctx.request_context.lifespan_context.docker.containers.get(container_id)
    return container.logs(tail=100).decode("utf-8")


@app.resource(
    "docker://containers/{container_id}/stats",
    name="Container stats",
    description="Live resource usage stats for a container",
    mime_type="application/json",
)
def container_stats(container_id: str, ctx: Context) -> dict[str, Any]:
    container = ctx.request_context.lifespan_context.docker.containers.get(container_id)
    return container.stats(stream=False)


@app.tool(
    description="List all Docker containers",
    annotations=ToolAnnotations(
        read_only_hint=True, idempotent_hint=True, open_world_hint=False
    ),
)
def list_containers(
    ctx: Context[AppContext],
    all: Annotated[
        bool, Field(description="Show all containers (default shows just running)")
    ] = False,
    filters: Annotated[
        ListContainersFilters | None, Field(description="Filter containers")
    ] = None,
) -> list[dict[str, Any]]:
    return [
        docker_to_dict(container)
        for container in _client(ctx).containers.list(
            all=all, filters=filters.model_dump() if filters else None
        )
    ]


@app.tool(
    description="Create a new Docker container",
    annotations=ToolAnnotations(
        destructive_hint=False, idempotent_hint=False, open_world_hint=False
    ),
)
def create_container(
    ctx: Context[AppContext],
    image: ImageName,
    detach: Annotated[
        bool, Field(description="Run container in the background")
    ] = True,
    name: Annotated[str | None, Field(description="Container name")] = None,
    entrypoint: Annotated[
        str | None, Field(description="Entrypoint to run in container")
    ] = None,
    command: Annotated[
        str | None, Field(description="Command to run in container")
    ] = None,
    network: Annotated[
        str | None, Field(description="Network to attach the container to")
    ] = None,
    environment: Annotated[
        dict[str, str] | None, Field(description="Environment variables dictionary")
    ] = None,
    ports: Annotated[
        dict[str, int | list[int] | tuple[str, int] | None] | None,
        Field(description="Container-to-host port bindings"),
    ] = None,
    volumes: Annotated[
        dict[str, dict[str, str]] | list[str] | None,
        Field(description="Volume mappings"),
    ] = None,
    labels: Annotated[
        dict[str, str] | list[str] | None, Field(description="Container labels")
    ] = None,
    auto_remove: Annotated[
        bool, Field(description="Automatically remove the container")
    ] = False,
) -> dict[str, Any]:
    return docker_to_dict(
        _client(ctx).containers.create(
            image=image,
            detach=detach,
            name=name,
            entrypoint=entrypoint,
            command=command,
            network=network,
            environment=environment,
            ports=ports,
            volumes=volumes,
            labels=labels,
            auto_remove=auto_remove,
        )
    )


@app.tool(
    description="Run an image in a new Docker container (preferred over `create_container` + `start_container`)",
    annotations=ToolAnnotations(
        destructive_hint=False, idempotent_hint=False, open_world_hint=False
    ),
)
def run_container(
    ctx: Context[AppContext],
    image: ImageName,
    detach: Annotated[
        bool, Field(description="Run container in the background")
    ] = True,
    name: Annotated[str | None, Field(description="Container name")] = None,
    entrypoint: Annotated[
        str | None, Field(description="Entrypoint to run in container")
    ] = None,
    command: Annotated[
        str | None, Field(description="Command to run in container")
    ] = None,
    network: Annotated[
        str | None, Field(description="Network to attach the container to")
    ] = None,
    environment: Annotated[
        dict[str, str] | None, Field(description="Environment variables dictionary")
    ] = None,
    ports: Annotated[
        dict[str, int | list[int] | tuple[str, int] | None] | None,
        Field(description="Container-to-host port bindings"),
    ] = None,
    volumes: Annotated[
        dict[str, dict[str, str]] | list[str] | None,
        Field(description="Volume mappings"),
    ] = None,
    labels: Annotated[
        dict[str, str] | list[str] | None, Field(description="Container labels")
    ] = None,
    auto_remove: Annotated[
        bool, Field(description="Automatically remove the container")
    ] = False,
) -> dict[str, Any]:
    return docker_to_dict(
        _client(ctx).containers.run(
            image=image,
            detach=detach,
            name=name,
            entrypoint=entrypoint,
            command=command,
            network=network,
            environment=environment,
            ports=ports,
            volumes=volumes,
            labels=labels,
            auto_remove=auto_remove,
        )
    )


@app.tool(
    description="Stop and remove a container, then run a new container. Fails if the container does not exist.",
    annotations=ToolAnnotations(
        destructive_hint=True, idempotent_hint=False, open_world_hint=False
    ),
)
def recreate_container(
    ctx: Context[AppContext],
    image: ImageName,
    container_id: ContainerID | None = None,
    name: Annotated[str | None, Field(description="Container name")] = None,
    detach: Detach = True,
    entrypoint: Entrypoint = None,
    command: ContainerCommand = None,
    network: NetworkName = None,
    environment: Environment = None,
    ports: PortBindings = None,
    volumes: VolumeMappings = None,
    labels: ContainerLabels = None,
    auto_remove: AutoRemove = False,
) -> dict[str, Any]:
    if container_id is None and name is None:
        raise ValueError(
            "container_id or name is required for identifying the container to stop+remove"
        )
    old = _client(ctx).containers.get(container_id or name)
    old.stop()
    old.remove()
    return docker_to_dict(
        _client(ctx).containers.run(
            image=image,
            detach=detach,
            name=name,
            entrypoint=entrypoint,
            command=command,
            network=network,
            environment=environment,
            ports=ports,
            volumes=volumes,
            labels=labels,
            auto_remove=auto_remove,
        )
    )


@app.tool(
    description="Start a Docker container",
    annotations=ToolAnnotations(
        destructive_hint=False, idempotent_hint=False, open_world_hint=False
    ),
)
def start_container(
    ctx: Context[AppContext], container_id: ContainerID
) -> dict[str, Any]:
    container = _client(ctx).containers.get(container_id)
    container.start()
    return docker_to_dict(container)


@app.tool(
    description="Fetch logs for a Docker container",
    annotations=ToolAnnotations(
        read_only_hint=True, idempotent_hint=True, open_world_hint=False
    ),
)
def fetch_container_logs(
    ctx: Context[AppContext],
    container_id: ContainerID,
    tail: Annotated[
        int | Literal["all"],
        Field(description="Number of lines to show from the end"),
    ] = 100,
) -> dict[str, list[str]]:
    return {
        "logs": _client(ctx)
        .containers.get(container_id)
        .logs(tail=tail)
        .decode("utf-8")
        .split("\n")
    }


@app.tool(
    description="Stop a Docker container",
    annotations=ToolAnnotations(
        destructive_hint=False, idempotent_hint=False, open_world_hint=False
    ),
)
def stop_container(
    ctx: Context[AppContext], container_id: ContainerID
) -> dict[str, Any]:
    container = _client(ctx).containers.get(container_id)
    container.stop()
    return docker_to_dict(container)


@app.tool(
    description="Remove a Docker container",
    annotations=ToolAnnotations(
        destructive_hint=True, idempotent_hint=False, open_world_hint=False
    ),
)
def remove_container(
    ctx: Context[AppContext],
    container_id: ContainerID,
    force: Annotated[bool, Field(description="Force remove the container")] = False,
) -> dict[str, Any]:
    container = _client(ctx).containers.get(container_id)
    container.remove(force=force)
    return docker_to_dict(container, {"status": "removed"})


@app.tool(
    description="List Docker images",
    annotations=ToolAnnotations(
        read_only_hint=True, idempotent_hint=True, open_world_hint=False
    ),
)
def list_images(
    ctx: Context[AppContext],
    name: Annotated[
        str | None, Field(description="Filter images by repository name")
    ] = None,
    all: Annotated[
        bool, Field(description="Show all images (default hides intermediate)")
    ] = False,
    filters: Annotated[
        ListImagesFilters | None, Field(description="Filter images")
    ] = None,
) -> list[dict[str, Any]]:
    return [
        docker_to_dict(image)
        for image in _client(ctx).images.list(
            name=name, all=all, filters=filters.model_dump() if filters else None
        )
    ]


@app.tool(
    description="Pull a Docker image",
    annotations=ToolAnnotations(
        destructive_hint=False, idempotent_hint=False, open_world_hint=True
    ),
)
def pull_image(
    ctx: Context[AppContext],
    repository: Annotated[str, Field(description="Image repository")],
    tag: Annotated[str | None, Field(description="Image tag")] = "latest",
) -> dict[str, Any]:
    return docker_to_dict(_client(ctx).images.pull(repository, tag=tag))


@app.tool(
    description="Push a Docker image",
    annotations=ToolAnnotations(
        destructive_hint=False, idempotent_hint=False, open_world_hint=True
    ),
)
def push_image(
    ctx: Context[AppContext],
    repository: Annotated[str, Field(description="Image repository")],
    tag: Annotated[str | None, Field(description="Image tag")] = "latest",
) -> dict[str, str | None]:
    _client(ctx).images.push(repository, tag=tag)
    return {"status": "pushed", "repository": repository, "tag": tag}


@app.tool(
    description="Build a Docker image from a Dockerfile",
    annotations=ToolAnnotations(
        destructive_hint=False, idempotent_hint=False, open_world_hint=False
    ),
)
def build_image(
    ctx: Context[AppContext],
    path: Annotated[str, Field(description="Path to build context")],
    tag: Annotated[str, Field(description="Image tag")],
    dockerfile: Annotated[str | None, Field(description="Path to Dockerfile")] = None,
) -> dict[str, Any]:
    image, logs = _client(ctx).images.build(
        path=path, tag=tag, dockerfile=dockerfile, rm=True, forcerm=True
    )
    return {"image": docker_to_dict(image), "logs": list(logs)}


@app.tool(
    description="Remove a Docker image",
    annotations=ToolAnnotations(
        destructive_hint=True, idempotent_hint=False, open_world_hint=False
    ),
)
def remove_image(
    ctx: Context[AppContext],
    image: Annotated[str, Field(description="Image ID or name")],
    force: Annotated[bool, Field(description="Force remove the image")] = False,
) -> dict[str, str]:
    _client(ctx).images.remove(image=image, force=force)
    return {"status": "removed", "image": image}


@app.tool(
    description="List Docker networks",
    annotations=ToolAnnotations(
        read_only_hint=True, idempotent_hint=True, open_world_hint=False
    ),
)
def list_networks(
    ctx: Context[AppContext],
    filters: Annotated[
        ListNetworksFilter | None, Field(description="Filter networks")
    ] = None,
) -> list[dict[str, Any]]:
    return [
        docker_to_dict(network)
        for network in _client(ctx).networks.list(
            filters=filters.model_dump() if filters else None
        )
    ]


@app.tool(
    description="Create a Docker network",
    annotations=ToolAnnotations(
        destructive_hint=False, idempotent_hint=False, open_world_hint=False
    ),
)
def create_network(
    ctx: Context[AppContext],
    name: Annotated[str, Field(description="Network name")],
    driver: Annotated[str | None, Field(description="Network driver")] = "bridge",
    internal: Annotated[bool, Field(description="Create an internal network")] = False,
    labels: Annotated[
        dict[str, str] | None, Field(description="Network labels")
    ] = None,
) -> dict[str, Any]:
    return docker_to_dict(
        _client(ctx).networks.create(
            name=name, driver=driver, internal=internal, labels=labels
        )
    )


@app.tool(
    description="Remove a Docker network",
    annotations=ToolAnnotations(
        destructive_hint=True, idempotent_hint=False, open_world_hint=False
    ),
)
def remove_network(
    ctx: Context[AppContext],
    network_id: Annotated[str, Field(description="Network ID or name")],
) -> dict[str, Any]:
    network = _client(ctx).networks.get(network_id)
    network.remove()
    return docker_to_dict(network)


@app.tool(
    description="List Docker volumes",
    annotations=ToolAnnotations(
        read_only_hint=True, idempotent_hint=True, open_world_hint=False
    ),
)
def list_volumes(ctx: Context[AppContext]) -> list[dict[str, Any]]:
    return [docker_to_dict(volume) for volume in _client(ctx).volumes.list()]


@app.tool(
    description="Create a Docker volume",
    annotations=ToolAnnotations(
        destructive_hint=False, idempotent_hint=False, open_world_hint=False
    ),
)
def create_volume(
    ctx: Context[AppContext],
    name: Annotated[str, Field(description="Volume name")],
    driver: Annotated[str | None, Field(description="Volume driver")] = "local",
    labels: Annotated[dict[str, str] | None, Field(description="Volume labels")] = None,
) -> dict[str, Any]:
    return docker_to_dict(
        _client(ctx).volumes.create(name=name, driver=driver, labels=labels)
    )


@app.tool(
    description="Remove a Docker volume",
    annotations=ToolAnnotations(
        destructive_hint=True, idempotent_hint=False, open_world_hint=False
    ),
)
def remove_volume(
    ctx: Context[AppContext],
    volume_name: Annotated[str, Field(description="Volume name")],
    force: Annotated[bool, Field(description="Force remove the volume")] = False,
) -> dict[str, Any]:
    volume = _client(ctx).volumes.get(volume_name)
    volume.remove(force=force)
    return docker_to_dict(volume)
