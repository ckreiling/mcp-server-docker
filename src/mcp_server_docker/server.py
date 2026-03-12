import json
from collections.abc import Sequence
from typing import Any
import traceback

import docker
import mcp.types as types
from docker.models.containers import Container
from mcp.server import Server
from pydantic import AnyUrl, ValidationError

from .input_schemas import (
    BuildImageInput,
    ContainerActionInput,
    CreateContainerInput,
    CreateNetworkInput,
    CreateVolumeInput,
    DockerComposePromptInput,
    FetchContainerLogsInput,
    ListContainersInput,
    ListImagesInput,
    ListNetworksInput,
    ListVolumesInput,
    PullPushImageInput,
    RecreateContainerInput,
    RemoveContainerInput,
    RemoveImageInput,
    RemoveNetworkInput,
    RemoveVolumeInput,
)
from .output_schemas import docker_to_dict
from .settings import ServerSettings

app = Server("docker-server")
_docker: docker.DockerClient
_server_settings: ServerSettings


@app.list_prompts()
async def list_prompts() -> list[types.Prompt]:
    return [
        types.Prompt(
            name="docker_compose",
            description="Treat the LLM like a Docker Compose manager",
            arguments=[
                types.PromptArgument(
                    name="name", description="Unique name of the project", required=True
                ),
                types.PromptArgument(
                    name="containers",
                    description="Describe containers you want",
                    required=True,
                ),
            ],
        )
    ]


@app.get_prompt()
async def get_prompt(
    name: str, arguments: dict[str, str] | None
) -> types.GetPromptResult:
    if name == "docker_compose":
        input = DockerComposePromptInput.model_validate(arguments)
        project_label = f"mcp-server-docker.project={input.name}"
        containers: list[Container] = _docker.containers.list(
            filters={"label": project_label}
        )
        volumes = _docker.volumes.list(filters={"label": project_label})
        networks = _docker.networks.list(filters={"label": project_label})

        return types.GetPromptResult(
            messages=[
                types.PromptMessage(
                    role="user",
                    content=types.TextContent(
                        type="text",
                        text=f"""
You are going to act as a Docker Compose manager, using the Docker Tools
available to you. Instead of being provided a `docker-compose.yml` file,
you will be given instructions in plain language, and interact with the
user through a plan+apply loop, akin to how Terraform operates.

Every Docker resource you create must be assigned the following label:

    {project_label}

You should use this label to filter resources when possible.

Every Docker resource you create must also be prefixed with the project name, followed by a dash (`-`):

    {input.name}-{{ResourceName}}

Here are the resources currently present in the project, based on the presence of the above label:

<BEGIN CONTAINERS>
{json.dumps([docker_to_dict(c) for c in containers], indent=2)}
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
{input.containers}
<END DOCKER-RESOURCES>

Respond to this message with a plan of what you will do, in the EXACT format below:

<BEGIN FORMAT>
## Introduction

I will be assisting with deploying Docker containers for project: `{input.name}`.

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
""",
                    ),
                )
            ]
        )

    raise ValueError(f"Unknown prompt name: {name}")


@app.list_resources()
async def list_resources() -> list[types.Resource]:
    resources = []
    for container in _docker.containers.list():
        resources.extend(
            [
                types.Resource(
                    uri=AnyUrl(f"docker://containers/{container.id}/logs"),
                    name=f"Logs for {container.name}",
                    description=f"Live logs for container {container.name}",
                    mimeType="text/plain",
                ),
                types.Resource(
                    uri=AnyUrl(f"docker://containers/{container.id}/stats"),
                    name=f"Stats for {container.name}",
                    description=f"Live resource usage stats for container {container.name}",
                    mimeType="application/json",
                ),
            ]
        )
    return resources


@app.read_resource()
async def read_resource(uri: AnyUrl) -> str:
    if not str(uri).startswith("docker://containers/"):
        raise ValueError(f"Unknown resource URI: {uri}")

    parts = str(uri).split("/")
    if len(parts) != 5:  # docker://containers/{id}/{logs|stats}
        raise ValueError(f"Invalid container resource URI: {uri}")

    container_id = parts[3]
    resource_type = parts[4]
    container = _docker.containers.get(container_id)

    if resource_type == "logs":
        logs = container.logs(tail=100).decode("utf-8")
        return json.dumps(logs.split("\n"))

    elif resource_type == "stats":
        stats = container.stats(stream=False)
        return json.dumps(stats, indent=2)

    else:
        raise ValueError(f"Unknown container resource type: {resource_type}")


@app.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="list_containers",
            description="List all Docker containers on the system, including running and stopped containers. Use when the user wants to view, inspect, or get an overview of existing containers and their current states. Do not use when you need to see available Docker images (use list_images instead) or when you want to create a new container (use create_container instead). Accepts optional filtering parameters such as `status` (e.g., "running", "exited") and `name` for specific container matching. e.g., filtering by status="running" to show only active containers. Raises an error if Docker daemon is not running or accessible.",
            inputSchema=ListContainersInput.model_json_schema(),
        ),
        types.Tool(
            name="create_container",
            description="Create a new Docker container from an existing image without starting it. Use when the user wants to prepare a container with specific configuration before manually starting it later. Do not use when you need to create and immediately run a container (use run_container instead). Accepts `image` (required), `name` (optional container name), `ports` (optional port mappings), and `environment` (optional env vars). e.g., image="nginx:latest", name="my-web-server". Raises an error if the specified image does not exist locally.",
            inputSchema=CreateContainerInput.model_json_schema(),
        ),
        types.Tool(
            name="run_container",
            description="Run a Docker image in a new container with a single command. Use when the user wants to quickly start a containerized application or service from an existing image. Do not use when you need fine-grained control over container creation and startup (use create_container then start_container instead). Accepts `image` (required), `name` (optional container name), `ports` (optional port mappings), and `environment` (optional env vars). e.g., image="nginx:latest", ports=["80:8080"], name="my-web-server". Raises an error if the specified image does not exist locally or cannot be pulled.",
            inputSchema=CreateContainerInput.model_json_schema(),
        ),
        types.Tool(
            name="recreate_container",
            description="Recreate a Docker container by stopping, removing, and running a fresh instance with the same configuration. Use when the user wants to reset a container's state, apply configuration changes, or resolve issues that require a clean restart. Do not use when you only need to restart an existing container (use stop_container then start_container instead) or create a brand new container (use run_container instead). Accepts `container_name` or `container_id` (required), e.g., "web-server" or "nginx-prod". Fails if the specified container does not exist or cannot be stopped.",
            inputSchema=RecreateContainerInput.model_json_schema(),
        ),
        types.Tool(
            name="start_container",
            description="Start a Docker container that already exists but is currently stopped. Use when the user wants to resume or restart a previously created container that has been stopped. Do not use when you need to create and run a new container from an image (use run_container instead) or when the container doesn't exist yet (use create_container first). Accepts `container_id` or `container_name` (required), e.g., "my-web-app" or "3f2c1a8b9e4d". Raises an error if the container is already running or does not exist.",
            inputSchema=ContainerActionInput.model_json_schema(),
        ),
        types.Tool(
            name="fetch_container_logs",
            description="Fetch logs from a running or stopped Docker container for debugging and monitoring purposes. Use when the user wants to view container output, debug application issues, or monitor container behavior. Do not use when you need to see which containers are available (use list_containers instead). Accepts `container_id` or `container_name` (required), `lines` (optional number of recent lines), and `follow` (optional boolean for streaming). e.g., container_name="web-server", lines=100. Raises an error if the container does not exist or has been removed.",
            inputSchema=FetchContainerLogsInput.model_json_schema(),
        ),
        types.Tool(
            name="stop_container",
            description="Stop a running Docker container by gracefully shutting it down. Use when the user wants to halt a currently running container without removing it from the system. Do not use when you need to permanently delete the container (use remove_container instead) or when you want to stop and immediately restart it (use recreate_container instead). Accepts `container_id` or `container_name` (required), e.g., "my-web-app" or "a1b2c3d4e5f6". Raises an error if the container is already stopped or does not exist.",
            inputSchema=ContainerActionInput.model_json_schema(),
        ),
        types.Tool(
            name="remove_container",
            description="Remove a Docker container from the system. Use when the user wants to permanently delete a stopped container to free up disk space or clean up unused containers. Do not use when the container is still running (use stop_container first) or when you need to recreate it immediately (use recreate_container instead). Accepts `container_id` or `container_name` (required), e.g., "my-app-container" or "a1b2c3d4e5f6". Raises an error if the container is currently running or does not exist.",
            inputSchema=RemoveContainerInput.model_json_schema(),
        ),
        types.Tool(
            name="list_images",
            description="List all Docker images available on the local system. Use when the user wants to view, inspect, or inventory available Docker images before running containers or managing storage. Do not use when you need to download a new image (use pull_image instead) or create an image (use build_image instead). Accepts optional filtering parameters such as `repository` and `tag`. e.g., filtering by "nginx" repository or "latest" tag. Fails if Docker daemon is not running or accessible.",
            inputSchema=ListImagesInput.model_json_schema(),
        ),
        types.Tool(
            name="pull_image",
            description="Pull a Docker image from a registry to the local Docker daemon. Use when the user wants to download or update an image before creating containers from it. Do not use when you need to create and run a container immediately (use run_container instead). Accepts `image` (required, string with optional tag), e.g., "nginx:latest" or "ubuntu:20.04". Raises an error if the image does not exist in the registry or network connectivity fails.",
            inputSchema=PullPushImageInput.model_json_schema(),
        ),
        types.Tool(
            name="push_image",
            description="Push a Docker image to a registry or repository. Use when the user wants to upload a locally built or tagged image to Docker Hub, a private registry, or another remote repository for sharing or deployment. Do not use when you need to download an image from a registry (use pull_image instead). Accepts `image` (required, image name with optional tag), `registry` (optional, defaults to Docker Hub), and `tag` (optional, defaults to "latest"). e.g., image="myapp:v1.0" or "registry.example.com/myapp". Raises an error if the image does not exist locally or authentication credentials are invalid.",
            inputSchema=PullPushImageInput.model_json_schema(),
        ),
        types.Tool(
            name="build_image",
            description="Build a Docker image from a Dockerfile in the current directory or specified path. Use when the user wants to create a custom Docker image from source code, configuration files, or application dependencies. Do not use when you need to download an existing image from a registry (use pull_image instead). Accepts `dockerfile_path` (optional, defaults to "./Dockerfile"), `image_name` (required), and `build_context` (optional, defaults to current directory). e.g., image_name="myapp:latest", dockerfile_path="./docker/Dockerfile". Raises an error if the Dockerfile contains invalid syntax or required base images are unavailable.",
            inputSchema=BuildImageInput.model_json_schema(),
        ),
        types.Tool(
            name="remove_image",
            description="Remove a Docker image from the local system. Use when the user wants to delete unused images to free up disk space or clean up after development work. Do not use when you need to download an image from a registry (use pull_image instead). Accepts `image` (required: image name or ID), and `force` (optional: boolean to force removal). e.g., image="nginx:latest" or image="sha256:abc123". Raises an error if the image is currently being used by a running container.",
            inputSchema=RemoveImageInput.model_json_schema(),
        ),
        types.Tool(
            name="list_networks",
            description="List all Docker networks available on the system. Use when the user wants to inspect, review, or troubleshoot network configurations and connectivity between containers. Do not use when you need to create a new network (use create_network instead) or remove an existing network (use remove_network instead). Accepts optional filtering parameters such as `name` and `driver` type. e.g., filtering by bridge networks or custom network names like "my-app-network". Fails if Docker daemon is not running or accessible.",
            inputSchema=ListNetworksInput.model_json_schema(),
        ),
        types.Tool(
            name="create_network",
            description="Create a Docker network to enable communication between containers. Use when the user wants to establish isolated networking for multi-container applications or microservices. Do not use when you need to view existing networks (use list_networks instead) or remove a network (use remove_network instead). Accepts `name` (required), `driver` (optional: "bridge", "host", "overlay"), and `options` (optional key-value pairs). e.g., name="my-app-network", driver="bridge". Raises an error if a network with the same name already exists.",
            inputSchema=CreateNetworkInput.model_json_schema(),
        ),
        types.Tool(
            name="remove_network",
            description="Remove a Docker network from the system. Use when the user wants to clean up unused networks or delete a specific network that is no longer needed. Do not use when you need to see available networks first (use list_networks instead). Accepts `network_name` or `network_id` (required), e.g., "my-custom-network" or "bridge". Raises an error if the network is still in use by running containers or does not exist.",
            inputSchema=RemoveNetworkInput.model_json_schema(),
        ),
        types.Tool(
            name="list_volumes",
            description="List all Docker volumes on the system. Use when the user wants to inspect, review, or manage persistent data storage volumes. Do not use when you need to create a new volume (use create_volume instead). Accepts optional filter parameters such as `dangling` (boolean) and `driver` (string). e.g., dangling=true to show unused volumes or driver="local" for local volumes. Raises an error if the Docker daemon is not running or accessible.",
            inputSchema=ListVolumesInput.model_json_schema(),
        ),
        types.Tool(
            name="create_volume",
            description="Create a Docker volume for persistent data storage that survives container restarts and removals. Use when the user wants to store application data, database files, or configuration that must persist across container lifecycles. Accepts `name` (required string for volume identifier) and `driver` (optional, defaults to "local"). e.g., name="postgres_data" or name="app_logs". Do not use when you need to mount host directories directly into containers (use create_container with bind mounts instead). Raises an error if a volume with the same name already exists.",
            inputSchema=CreateVolumeInput.model_json_schema(),
        ),
        types.Tool(
            name="remove_volume",
            description="Remove a Docker volume from the system permanently. Use when the user wants to delete an unused volume to free up disk space or clean up after container removal. Accepts `volume_name` (required string), e.g., "my-data-volume" or "postgres_data". Do not use when you need to create or list volumes (use create_volume or list_volumes instead). Raises an error if the volume is currently in use by a running container or if the volume does not exist.",
            inputSchema=RemoveVolumeInput.model_json_schema(),
        ),
    ]


@app.call_tool()
async def call_tool(
    name: str, arguments: Any
) -> Sequence[types.TextContent | types.ImageContent | types.EmbeddedResource]:
    if arguments is None:
        arguments = {}

    result = None

    try:
        if name == "list_containers":
            args = ListContainersInput(**arguments)
            containers = _docker.containers.list(**args.model_dump())
            result = [docker_to_dict(c) for c in containers]

        elif name == "create_container":
            args = CreateContainerInput(**arguments)
            container = _docker.containers.create(**args.model_dump())
            result = docker_to_dict(container)

        elif name == "run_container":
            args = CreateContainerInput(**arguments)
            container = _docker.containers.run(**args.model_dump())
            result = docker_to_dict(container)

        elif name == "recreate_container":
            args = RecreateContainerInput(**arguments)

            container = _docker.containers.get(args.resolved_container_id)
            container.stop()
            container.remove()

            run_args = CreateContainerInput(**arguments)
            container = _docker.containers.run(**run_args.model_dump())
            result = docker_to_dict(container)

        elif name == "start_container":
            args = ContainerActionInput(**arguments)
            container = _docker.containers.get(args.container_id)
            container.start()
            result = docker_to_dict(container)

        elif name == "stop_container":
            args = ContainerActionInput(**arguments)
            container = _docker.containers.get(args.container_id)
            container.stop()
            result = docker_to_dict(container)

        elif name == "remove_container":
            args = RemoveContainerInput(**arguments)
            container = _docker.containers.get(args.container_id)
            container.remove(force=args.force)
            result = docker_to_dict(container, {"status": "removed"})

        elif name == "fetch_container_logs":
            args = FetchContainerLogsInput(**arguments)
            container = _docker.containers.get(args.container_id)
            logs = container.logs(tail=args.tail).decode("utf-8")
            result = {"logs": logs.split("\n")}

        elif name == "list_images":
            args = ListImagesInput(**arguments)

            images = _docker.images.list(**args.model_dump())
            result = [docker_to_dict(img) for img in images]

        elif name == "pull_image":
            args = PullPushImageInput(**arguments)
            model_dump = args.model_dump()
            repository = model_dump.pop("repository")
            image = _docker.images.pull(repository, **model_dump)
            result = docker_to_dict(image)

        elif name == "push_image":
            args = PullPushImageInput(**arguments)
            model_dump = args.model_dump()
            repository = model_dump.pop("repository")
            _docker.images.push(repository, **model_dump)
            result = {
                "status": "pushed",
                "repository": args.repository,
                "tag": args.tag,
            }

        elif name == "build_image":
            args = BuildImageInput(**arguments)
            image, logs = _docker.images.build(**args.model_dump())
            result = {"image": docker_to_dict(image), "logs": list(logs)}

        elif name == "remove_image":
            args = RemoveImageInput(**arguments)
            _docker.images.remove(**args.model_dump())
            result = {"status": "removed", "image": args.image}

        elif name == "list_networks":
            args = ListNetworksInput(**arguments)
            networks = _docker.networks.list(**args.model_dump())
            result = [docker_to_dict(net) for net in networks]

        elif name == "create_network":
            args = CreateNetworkInput(**arguments)
            network = _docker.networks.create(**args.model_dump())
            result = docker_to_dict(network)

        elif name == "remove_network":
            args = RemoveNetworkInput(**arguments)
            network = _docker.networks.get(args.network_id)
            network.remove()
            result = docker_to_dict(network)

        elif name == "list_volumes":
            ListVolumesInput(**arguments)  # Validate empty input
            volumes = _docker.volumes.list()
            result = [docker_to_dict(v) for v in volumes]

        elif name == "create_volume":
            args = CreateVolumeInput(**arguments)
            volume = _docker.volumes.create(**args.model_dump())
            result = docker_to_dict(volume)

        elif name == "remove_volume":
            args = RemoveVolumeInput(**arguments)
            volume = _docker.volumes.get(args.volume_name)
            volume.remove(force=args.force)
            result = docker_to_dict(volume)

        else:
            return [types.TextContent(type="text", text=f"Unknown tool: {name}")]

    except ValidationError as e:
        await app.request_context.session.send_log_message(
            "error", "Failed to validate input provided by LLM: " + str(e)
        )
        return [
            types.TextContent(
                type="text", text=f"ERROR: You provided invalid Tool inputs: {e}"
            )
        ]

    except Exception as e:
        await app.request_context.session.send_log_message(
            "error", traceback.format_exc()
        )
        raise e

    return [types.TextContent(type="text", text=json.dumps(result, indent=2))]


async def run_stdio(settings: ServerSettings, docker_client: docker.DockerClient):
    """Run the server on Standard I/O with the given settings and Docker client."""
    from mcp.server.stdio import stdio_server

    global _docker
    _docker = docker_client

    global _server_settings
    _server_settings = settings

    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())
