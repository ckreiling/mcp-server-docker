import json
from collections.abc import Sequence
from typing import Any

import docker
import docker.errors
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

app = Server("docker-server")
docker_client = docker.from_env()


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
        containers: list[Container] = docker_client.containers.list(
            filters={"label": project_label}
        )
        volumes = docker_client.volumes.list(filters={"label": project_label})
        networks = docker_client.networks.list(filters={"label": project_label})

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
{json.dumps([{"name": c.name, "image": {"id": c.image.id, "tags": c.image.tags} if c.image is not None else {}, "status": c.status, "id": c.id, "ports": c.ports, "health": c.health} for c in containers], indent=2)}
<END CONTAINERS>
<BEGIN VOLUMES>
{json.dumps([{"name": v.name, "id": v.id} for v in volumes], indent=2)}
<END VOLUMES>
<BEGIN NETWORKS>
{json.dumps([{"name": n.name, "id": n.id, "containers": [{"id": c.id} for c in n.containers]} for n in networks], indent=2)}
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
    for container in docker_client.containers.list():
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
    container = docker_client.containers.get(container_id)

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
            description="List all Docker containers",
            inputSchema=ListContainersInput.model_json_schema(),
        ),
        types.Tool(
            name="create_container",
            description="Create a new Docker container",
            inputSchema=CreateContainerInput.model_json_schema(),
        ),
        types.Tool(
            name="run_container",
            description="Run an image in a new Docker container",
            inputSchema=CreateContainerInput.model_json_schema(),
        ),
        types.Tool(
            name="recreate_container",
            description="Stop and remove a container, then run a new container. Fails if the container does not exist.",
            inputSchema=RecreateContainerInput.model_json_schema(),
        ),
        types.Tool(
            name="start_container",
            description="Start a Docker container",
            inputSchema=ContainerActionInput.model_json_schema(),
        ),
        types.Tool(
            name="fetch_container_logs",
            description="Fetch logs for a Docker container",
            inputSchema=FetchContainerLogsInput.model_json_schema(),
        ),
        types.Tool(
            name="stop_container",
            description="Stop a Docker container",
            inputSchema=ContainerActionInput.model_json_schema(),
        ),
        types.Tool(
            name="remove_container",
            description="Remove a Docker container",
            inputSchema=RemoveContainerInput.model_json_schema(),
        ),
        types.Tool(
            name="list_images",
            description="List Docker images",
            inputSchema=ListImagesInput.model_json_schema(),
        ),
        types.Tool(
            name="pull_image",
            description="Pull a Docker image",
            inputSchema=PullPushImageInput.model_json_schema(),
        ),
        types.Tool(
            name="push_image",
            description="Push a Docker image",
            inputSchema=PullPushImageInput.model_json_schema(),
        ),
        types.Tool(
            name="build_image",
            description="Build a Docker image from a Dockerfile",
            inputSchema=BuildImageInput.model_json_schema(),
        ),
        types.Tool(
            name="remove_image",
            description="Remove a Docker image",
            inputSchema=RemoveImageInput.model_json_schema(),
        ),
        types.Tool(
            name="list_networks",
            description="List Docker networks",
            inputSchema=ListNetworksInput.model_json_schema(),
        ),
        types.Tool(
            name="create_network",
            description="Create a Docker network",
            inputSchema=CreateNetworkInput.model_json_schema(),
        ),
        types.Tool(
            name="remove_network",
            description="Remove a Docker network",
            inputSchema=RemoveNetworkInput.model_json_schema(),
        ),
        types.Tool(
            name="list_volumes",
            description="List Docker volumes",
            inputSchema=ListVolumesInput.model_json_schema(),
        ),
        types.Tool(
            name="create_volume",
            description="Create a Docker volume",
            inputSchema=CreateVolumeInput.model_json_schema(),
        ),
        types.Tool(
            name="remove_volume",
            description="Remove a Docker volume",
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
            args = ListContainersInput.model_validate(arguments)
            containers = docker_client.containers.list(**args.model_dump())
            result = [
                {
                    "id": c.id,
                    "name": c.name,
                    "status": c.status,
                    "image": c.image.tags,
                }
                for c in containers
            ]

        elif name == "create_container":
            args = CreateContainerInput.model_validate(arguments)
            container = docker_client.containers.create(**args.model_dump())
            result = {
                "status": container.status,
                "id": container.id,
                "name": container.name,
            }

        elif name == "run_container":
            args = CreateContainerInput.model_validate(arguments)
            container = docker_client.containers.run(**args.model_dump())
            result = {
                "status": container.status,
                "id": container.id,
                "name": container.name,
            }

        elif name == "recreate_container":
            args = RecreateContainerInput.model_validate(arguments)

            container = docker_client.containers.get(args.resolved_container_id)
            container.stop()
            container.remove()

            run_args = CreateContainerInput.model_validate(arguments)
            container = docker_client.containers.run(**run_args.model_dump())
            result = {
                "status": container.status,
                "id": container.id,
                "name": container.name,
            }

        elif name == "start_container":
            args = ContainerActionInput.model_validate(arguments)
            container = docker_client.containers.get(args.container_id)
            container.start()
            result = {"status": container.status, "id": container.id}

        elif name == "stop_container":
            args = ContainerActionInput.model_validate(arguments)
            container = docker_client.containers.get(args.container_id)
            container.stop()
            result = {"status": container.status, "id": container.id}

        elif name == "remove_container":
            args = RemoveContainerInput.model_validate(arguments)
            container = docker_client.containers.get(args.container_id)
            container.remove(force=args.force)
            result = {"status": "removed", "id": args.container_id}

        elif name == "fetch_container_logs":
            args = FetchContainerLogsInput.model_validate(arguments)
            container = docker_client.containers.get(args.container_id)
            logs = container.logs(tail=args.tail).decode("utf-8")
            result = {"logs": logs.split("\n")}

        elif name == "list_images":
            args = ListImagesInput.model_validate(arguments)

            images = docker_client.images.list(**args.model_dump())
            result = [{"id": img.id, "tags": img.tags} for img in images]

        elif name == "pull_image":
            args = PullPushImageInput.model_validate(arguments)
            model_dump = args.model_dump()
            repository = model_dump.pop("repository")
            image = docker_client.images.pull(repository, **model_dump)
            result = {"id": image.id, "tags": image.tags}

        elif name == "push_image":
            args = PullPushImageInput.model_validate(arguments)
            model_dump = args.model_dump()
            repository = model_dump.pop("repository")
            docker_client.images.push(repository, **model_dump)
            result = {
                "status": "pushed",
                "repository": args.repository,
                "tag": args.tag,
            }

        elif name == "build_image":
            args = BuildImageInput.model_validate(arguments)
            image, logs = docker_client.images.build(**args.model_dump())
            result = {"id": image.id, "tags": image.tags, "logs": list(logs)}

        elif name == "remove_image":
            args = RemoveImageInput.model_validate(arguments)
            docker_client.images.remove(**args.model_dump())
            result = {"status": "removed", "image": args.image}

        elif name == "list_networks":
            args = ListNetworksInput.model_validate(arguments)
            networks = docker_client.networks.list(**args.model_dump())
            result = [
                {"id": net.id, "name": net.name, "driver": net.attrs["Driver"]}
                for net in networks
            ]

        elif name == "create_network":
            args = CreateNetworkInput.model_validate(arguments)
            network = docker_client.networks.create(**args.model_dump())
            result = {"id": network.id, "name": network.name}

        elif name == "remove_network":
            args = RemoveNetworkInput.model_validate(arguments)
            network = docker_client.networks.get(args.network_id)
            network.remove()
            result = {"status": "removed", "id": args.network_id}

        elif name == "list_volumes":
            ListVolumesInput.model_validate(arguments)  # Validate empty input
            volumes = docker_client.volumes.list()
            result = [
                {"name": vol.name, "driver": vol.attrs["Driver"]} for vol in volumes
            ]

        elif name == "create_volume":
            args = CreateVolumeInput.model_validate(arguments)
            volume = docker_client.volumes.create(**args.model_dump())
            result = {"name": volume.name, "driver": volume.attrs["Driver"]}

        elif name == "remove_volume":
            args = RemoveVolumeInput.model_validate(arguments)
            volume = docker_client.volumes.get(args.volume_name)
            volume.remove(force=args.force)
            result = {"status": "removed", "name": args.volume_name}

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

    return [types.TextContent(type="text", text=json.dumps(result, indent=2))]


async def main():
    from mcp.server.stdio import stdio_server

    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())
