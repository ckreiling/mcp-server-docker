import asyncio
import os
import subprocess
import urllib.parse
from pathlib import Path

import docker
from paramiko.config import SSHConfig

from .server import run_stdio
from .settings import ServerSettings


def resolve_ssh_config(docker_host: str) -> str:
    """Resolve SSH config aliases in DOCKER_HOST environment variable."""
    if not docker_host or not docker_host.startswith("ssh://"):
        return docker_host
    
    # Parse the SSH URL
    parsed = urllib.parse.urlparse(docker_host)
    
    # If it already has username@hostname format, don't modify it
    if "@" in parsed.netloc:
        return docker_host
    
    # Load SSH config
    ssh_config_path = Path.home() / ".ssh" / "config"
    if not ssh_config_path.exists():
        return docker_host
    
    try:
        ssh_config = SSHConfig()
        with open(ssh_config_path) as f:
            ssh_config.parse(f)
        
        # Get config for the hostname
        hostname = parsed.hostname
        if not hostname:
            return docker_host
            
        host_config = ssh_config.lookup(hostname)
        
        # Build the resolved SSH URL
        resolved_hostname = host_config.get("hostname", hostname)
        user = host_config.get("user")
        port = host_config.get("port", "22")
        
        if user:
            netloc = f"{user}@{resolved_hostname}"
        else:
            netloc = resolved_hostname
            
        if port != "22":
            netloc += f":{port}"
        
        # Reconstruct the URL
        resolved_url = urllib.parse.urlunparse((
            parsed.scheme,
            netloc,
            parsed.path,
            parsed.params,
            parsed.query,
            parsed.fragment
        ))
        
        return resolved_url
        
    except Exception:
        # If anything goes wrong, return the original URL
        return docker_host


def _ensure_ssh_host_key_trusted(docker_host: str) -> None:
    """Ensure the SSH host key is trusted by adding it to known_hosts if needed."""
    try:
        from paramiko.config import SSHConfig
        import paramiko
        import subprocess
        
        # Parse the SSH URL to get host and port
        parsed = urllib.parse.urlparse(docker_host)
        hostname = parsed.hostname
        port = parsed.port or 22
        
        if not hostname:
            return
            
        # Check if we can resolve via SSH config
        ssh_config = SSHConfig()
        ssh_config_path = Path.home() / ".ssh" / "config"
        if ssh_config_path.exists():
            with open(ssh_config_path) as f:
                ssh_config.parse(f)
            host_config = ssh_config.lookup(hostname)
            hostname = host_config.get("hostname", hostname)
            port = int(host_config.get("port", port))
        
        # Try to add the host key using ssh-keyscan
        try:
            cmd = ["ssh-keyscan", "-p", str(port), hostname]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            
            if result.returncode == 0 and result.stdout.strip():
                known_hosts_path = Path.home() / ".ssh" / "known_hosts"
                known_hosts_path.parent.mkdir(mode=0o700, exist_ok=True)
                
                # Append the host key if it's not already there
                with open(known_hosts_path, "a+") as f:
                    f.seek(0)
                    existing_content = f.read()
                    
                    # Check if this host is already in known_hosts
                    host_line = f"[{hostname}]:{port}"
                    if host_line not in existing_content:
                        f.write(result.stdout)
                        
        except (subprocess.TimeoutExpired, subprocess.SubprocessError):
            # If ssh-keyscan fails, ignore the error
            pass
            
    except Exception:
        # If anything fails, don't prevent Docker client creation
        pass


def create_docker_client() -> docker.DockerClient:
    """Create a Docker client with SSH config resolution."""
    docker_host = os.environ.get("DOCKER_HOST", "")
    resolved_host = resolve_ssh_config(docker_host)
    
    # Set the resolved host back to environment if it was changed
    if resolved_host != docker_host:
        os.environ["DOCKER_HOST"] = resolved_host
    
    # For SSH connections, ensure host key is trusted
    if resolved_host and resolved_host.startswith("ssh://"):
        try:
            _ensure_ssh_host_key_trusted(resolved_host)
        except Exception:
            # Continue even if host key setup fails
            pass
    
    return docker.from_env()


def main():
    """Run the server sourcing configuration from environment variables."""
    asyncio.run(run_stdio(ServerSettings(), create_docker_client()))


# Optionally expose other important items at package level
__all__ = ["main", "run_stdio", "ServerSettings"]

