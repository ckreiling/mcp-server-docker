# Docker MCP server

An MCP server for Docker

## Components

### Resources

The server implements a couple resources for every container:

- Stats: CPU, memory, etc. for a container
- Logs: tail some logs from a container

### Prompts

#### `docker_compose`

Use natural language to compose containers.

Simply provide a project name and a description of the containers comprising the
project, and the LLM will come up with a deployment plan.

This prompt instructs the LLM to enter a `plan+apply` loop. Your interaction
with the LLM will involve the following steps:

1. You give the LLM instructions for which containers to bring up
2. The LLM calculates a concise natural language plan and presents it to you
3. You either:

- Apply the plan
- Provide the LLM feedback, and the LLM recalculates the plan

##### Examples

- name: `nginx`, containers: "deploy an nginx container exposing it on port
  9000"
- name: `wordpress`, containers: "deploy a WordPress container and a supporting
  MySQL container, exposing Wordpress on port 9000"

##### Resuming a Project

When starting a new chat with this prompt, the LLM will receive the status of
any containers, volumes, and networks created with the given project `name`.

This is mainly useful for cleaning up, in-case you lose a chat that was
responsible for many containers.

### Tools

#### Containers

- `list_containers`
- `create_container`
- `run_container`
- `recreate_container`
- `start_container`
- `fetch_container_logs`
- `stop_container`
- `remove_container`

#### Images

- `list_images`
- `pull_image`
- `push_image`
- `build_image`
- `remove_image`

#### Networks

- `list_networks`
- `create_network`
- `remove_network`

#### Volumes

- `list_volumes`
- `create_volume`
- `remove_volume`

## Disclaimers

### Sensitive Data

**DO NOT CONFIGURE CONTAINERS WITH SENSITIVE DATA.** This includes API keys,
database passwords, etc.

Any sensitive data exchanged with the LLM is inherently compromised, unless the
LLM is running on your local machine.

If you are interested in securely passing secrets to containers, file an issue
on this repository with your use-case.

### Reviewing Created Containers

Be careful to review the containers that the LLM creates. Docker is not a secure
sandbox, and therefore the MCP server can potentially impact the host machine
through Docker.

For safety reasons, this MCP server doesn't support sensitive Docker options
like `--privileged` or `--cap-add/--cap-drop`. If these features are of interest
to you, file an issue on this repository with your use-case.

## Configuration

This server uses the Python Docker SDK's `from_env` method. For configuration
details, see
[the documentation](https://docker-py.readthedocs.io/en/stable/client.html#docker.client.from_env).

## Quickstart

### Install

#### Claude Desktop

On MacOS: `~/Library/Application\ Support/Claude/claude_desktop_config.json`

On Windows: `%APPDATA%/Claude/claude_desktop_config.json`

<details>
  <summary>Development/Unpublished Servers Configuration</summary>

```
"mcpServers": {
  "mcp-server-docker": {
    "command": "uv",
    "args": [
      "--directory",
      "/path/to/repo",
      "run",
      "mcp-server-docker"
    ]
  }
}
```

</details>

<details>
  <summary>Docker-based Configuration</summary>

Run `docker build -t mcp-server-docker:latest -f Dockerfile .`

And then use this config:
```
"mcpServers": {
  "mcp-server-docker": {
    "command": "docker",
    "args": [
      "run",
      "-i",
      "--rm",
      "-v",
      "/var/run/docker.sock:/var/run/docker.sock",
      "mcp-server-docker:latest"
    ]
  }
}
```

</details>

## Development

Prefer using Devbox to configure your development environment.

See the `devbox.json` for helpful development commands.
