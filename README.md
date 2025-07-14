# 🐋 Docker MCP server

An MCP server for managing Docker with natural language!

## 🪩 What can it do?

- 🚀 Compose containers with natural language
- 🔍 Introspect & debug running containers
- 📀 Manage persistent data with Docker volumes
- ⚡ Execute commands inside running containers

## ❓ Who is this for?

- Server administrators: connect to remote Docker engines for e.g. managing a
  public-facing website.
- Tinkerers: run containers locally and experiment with open-source apps
  supporting Docker.
- AI enthusiasts: push the limits of that an LLM is capable of!

## Demo

A quick demo showing a WordPress deployment using natural language:

https://github.com/user-attachments/assets/65e35e67-bce0-4449-af7e-9f4dd773b4b3

## 🏎️ Quickstart

### Install

#### Claude Desktop

On MacOS: `~/Library/Application\ Support/Claude/claude_desktop_config.json`

On Windows: `%APPDATA%/Claude/claude_desktop_config.json`

<details>
  <summary>Install from PyPi with uv</summary>

If you don't have `uv` installed, follow the installation instructions for your
system:
[link](https://docs.astral.sh/uv/getting-started/installation/#installation-methods)

Then add the following to your MCP servers file:

```
"mcpServers": {
  "mcp-server-docker": {
    "command": "uvx",
    "args": [
      "mcp-server-docker"
    ]
  }
}
```

</details>

<details>
  <summary>Install with Docker</summary>

Purely for convenience, the server can run in a Docker container.

After cloning this repository, build the Docker image:

```bash
docker build -t mcp-server-docker .
```

And then add the following to your MCP servers file:

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

Note that we mount the Docker socket as a volume; this ensures the MCP server
can connect to and control the local Docker daemon.

</details>

## 📝 Prompts

### 🎻 `docker_compose`

Use natural language to compose containers. [See above](#demo) for a demo.

Provide a Project Name, and a description of desired containers, and let the LLM
do the rest.

This prompt instructs the LLM to enter a `plan+apply` loop. Your interaction
with the LLM will involve the following steps:

1. You give the LLM instructions for which containers to bring up
2. The LLM calculates a concise natural language plan and presents it to you
3. You either:
   - Apply the plan
   - Provide the LLM feedback, and the LLM recalculates the plan

#### Examples

- name: `nginx`, containers: "deploy an nginx container exposing it on port
  9000"
- name: `wordpress`, containers: "deploy a WordPress container and a supporting
  MySQL container, exposing Wordpress on port 9000"

#### Resuming a Project

When starting a new chat with this prompt, the LLM will receive the status of
any containers, volumes, and networks created with the given project `name`.

This is mainly useful for cleaning up, in-case you lose a chat that was
responsible for many containers.

## 📔 Resources

The server implements a couple resources for every container:

- Stats: CPU, memory, etc. for a container
- Logs: tail some logs from a container

## 🔨 Tools

### Containers

- `list_containers`
- `create_container`
- `run_container`
- `recreate_container`
- `start_container`
- `fetch_container_logs`
- `stop_container`
- `remove_container`
- `exec_in_container`

#### New: `exec_in_container`

The `exec_in_container` tool allows you to run arbitrary commands inside already running Docker containers. This is particularly useful for:

- Running development tools and scripts directly in containers
- Writing and executing unit tests within container environments
- Allowing AI tools like Claude to execute and verify code inside containers for correctness
- Debugging and troubleshooting running applications

**Usage Examples:**

```json
{
  "container_id": "my-container",
  "command": "ls -la /app",
  "working_dir": "/app",
  "user": "root"
}
```

```json
{
  "container_id": "web-server",
  "command": ["python", "-m", "pytest", "tests/"],
  "environment": {"PYTHONPATH": "/app"}
}
```

**Parameters:**
- `container_id`: Container ID or name (required)
- `command`: Command to execute (string or array of strings) (required)
- `working_dir`: Working directory for the command (optional)
- `environment`: Environment variables for the command (optional)
- `user`: User to run the command as (optional)
- `detach`: Run command in the background (optional, default: false)
- `stdin`: Attach stdin to the command (optional, default: false)
- `tty`: Allocate a pseudo-TTY (optional, default: false)
- `privileged`: Run with extended privileges (optional, default: false)
- `stream`: Stream output as it's generated (optional, default: false)
- `socket`: Return socket instead of output (optional, default: false)
- `demux`: Demultiplex stdout and stderr (optional, default: false)

### Images

- `list_images`
- `pull_image`
- `push_image`
- `build_image`
- `remove_image`

### Networks

- `list_networks`
- `create_network`
- `remove_network`

### Volumes

- `list_volumes`
- `create_volume`
- `remove_volume`

## 🚧 Disclaimers

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

## 🛠️ Configuration

This server uses the Python Docker SDK's `from_env` method. For configuration
details, see
[the documentation](https://docker-py.readthedocs.io/en/stable/client.html#docker.client.from_env).

### Connect to Docker over SSH

This MCP server can connect to a remote Docker daemon over SSH.

Simply set a `ssh://` host URL in the MCP server definition:

```
"mcpServers": {
  "mcp-server-docker": {
    "command": "uvx",
    "args": [
      "mcp-server-docker"
    ],
    "env": {
      "DOCKER_HOST": "ssh://myusername@myhost.example.com"
    }
  }
}
```

## 💻 Development

Prefer using Devbox to configure your development environment.

See the `devbox.json` for helpful development commands.

After setting up devbox you can configure your Claude MCP config to use it:

```
  "docker": {
    "command": "/path/to/repo/.devbox/nix/profile/default/bin/uv",
    "args": [
      "--directory",
      "/path/to/repo/",
      "run",
      "mcp-server-docker"
    ]
  },
```
