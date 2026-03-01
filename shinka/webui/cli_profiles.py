"""CLI Profile Managers for agent configuration.

Manages CLI-specific configurations (system prompts, MCP servers, allowed tools)
by writing to each CLI's native config format:
- Codex: ~/.codex/config.toml [profiles.shinka] section
- Gemini: ~/.gemini/settings.json
- Claude: ~/.claude/settings.json
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class MCPServer:
    """Model Context Protocol server configuration."""
    name: str
    command: str
    args: List[str] = field(default_factory=list)
    env: Optional[Dict[str, str]] = None

    def to_dict(self) -> Dict[str, Any]:
        d = {"name": self.name, "command": self.command, "args": self.args}
        if self.env:
            d["env"] = self.env
        return d


@dataclass
class CLIConfig:
    """Common configuration structure across all CLIs."""
    system_prompt: Optional[str] = None
    mcp_servers: List[MCPServer] = field(default_factory=list)
    allowed_tools: Optional[List[str]] = None
    extra_config: Dict[str, Any] = field(default_factory=dict)


class CodexProfileManager:
    """Manages [profiles.shinka] section in ~/.codex/config.toml.

    Codex CLI uses TOML format and supports profiles via [profiles.NAME] sections.
    We create/manage a 'shinka' profile that can be activated with --profile shinka.

    Note: Codex does NOT support MCP servers.
    """

    PROFILE_NAME = "shinka"

    def __init__(self, config_path: Optional[Path] = None):
        self.config_path = config_path or Path.home() / ".codex" / "config.toml"

    def _ensure_dir_exists(self) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)

    def _load_toml(self) -> Dict[str, Any]:
        """Load the config.toml file."""
        if not self.config_path.exists():
            return {}
        try:
            import tomllib
            return tomllib.loads(self.config_path.read_text())
        except ImportError:
            # Python < 3.11 fallback
            try:
                import toml
                return toml.load(self.config_path)
            except ImportError:
                logger.warning("Neither tomllib nor toml available, using basic parser")
                return self._basic_toml_parse()
        except Exception as e:
            logger.error(f"Failed to load codex config: {e}")
            return {}

    def _basic_toml_parse(self) -> Dict[str, Any]:
        """Very basic TOML parser for reading existing profiles."""
        if not self.config_path.exists():
            return {}
        content = self.config_path.read_text()
        result: Dict[str, Any] = {"profiles": {}}
        current_section = None

        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("[profiles.") and line.endswith("]"):
                profile_name = line[10:-1]
                result["profiles"][profile_name] = {}
                current_section = result["profiles"][profile_name]
            elif line.startswith("[") and line.endswith("]"):
                current_section = None
            elif "=" in line and current_section is not None:
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                current_section[key] = value
        return result

    def _save_toml(self, data: Dict[str, Any]) -> None:
        """Save the config.toml file, preserving existing content."""
        self._ensure_dir_exists()

        # Read existing content to preserve non-shinka sections
        existing_lines = []
        if self.config_path.exists():
            existing_lines = self.config_path.read_text().splitlines()

        # Find and remove existing [profiles.shinka] section
        new_lines = []
        in_shinka_section = False
        for line in existing_lines:
            stripped = line.strip()
            if stripped == f"[profiles.{self.PROFILE_NAME}]":
                in_shinka_section = True
                continue
            elif stripped.startswith("[") and in_shinka_section:
                in_shinka_section = False
            if not in_shinka_section:
                new_lines.append(line)

        # Add shinka profile section
        profile_data = data.get("profiles", {}).get(self.PROFILE_NAME, {})
        if profile_data:
            if new_lines and new_lines[-1].strip():
                new_lines.append("")
            new_lines.append(f"[profiles.{self.PROFILE_NAME}]")
            for key, value in profile_data.items():
                if isinstance(value, str):
                    # Escape quotes and use multiline for long strings
                    if "\n" in value:
                        new_lines.append(f'{key} = """')
                        new_lines.append(value)
                        new_lines.append('"""')
                    else:
                        escaped = value.replace('"', '\\"')
                        new_lines.append(f'{key} = "{escaped}"')
                elif isinstance(value, bool):
                    new_lines.append(f"{key} = {str(value).lower()}")
                elif isinstance(value, (int, float)):
                    new_lines.append(f"{key} = {value}")
                elif isinstance(value, list):
                    items = ", ".join(f'"{v}"' for v in value)
                    new_lines.append(f"{key} = [{items}]")

        self.config_path.write_text("\n".join(new_lines) + "\n")

    def load_config(self) -> CLIConfig:
        """Load the shinka profile configuration."""
        data = self._load_toml()
        profile = data.get("profiles", {}).get(self.PROFILE_NAME, {})

        return CLIConfig(
            system_prompt=profile.get("system_prompt"),
            mcp_servers=[],  # Codex doesn't support MCP
            allowed_tools=None,  # Codex doesn't support allowed_tools
            extra_config={k: v for k, v in profile.items() if k != "system_prompt"},
        )

    def save_config(self, config: CLIConfig) -> None:
        """Save the shinka profile configuration."""
        data = self._load_toml()
        if "profiles" not in data:
            data["profiles"] = {}

        profile_data = {}
        if config.system_prompt:
            profile_data["system_prompt"] = config.system_prompt
        profile_data.update(config.extra_config)

        data["profiles"][self.PROFILE_NAME] = profile_data
        self._save_toml(data)
        logger.info(f"Saved Codex shinka profile to {self.config_path}")

    def list_profiles(self) -> List[Dict[str, Any]]:
        """List all available profiles from config.toml.

        Returns:
            List of profile dicts with 'name', 'model', and other settings.
        """
        data = self._load_toml()
        profiles = []

        # Get root-level model as default profile
        root_model = data.get("model", "gpt-4.1-mini")
        profiles.append({
            "name": "default",
            "model": root_model,
            "is_default": True,
        })

        # Get all [profiles.*] sections
        profile_sections = data.get("profiles", {})
        for name, settings in profile_sections.items():
            if isinstance(settings, dict):
                profiles.append({
                    "name": name,
                    "model": settings.get("model", root_model),
                    "is_default": False,
                    **{k: v for k, v in settings.items() if k != "model"},
                })

        return profiles


class GeminiConfigManager:
    """Manages ~/.gemini/settings.json configuration.

    Gemini CLI uses JSON format with MCP servers in the 'mcpServers' key.
    We manage a 'shinka' section for our custom settings while preserving
    the rest of the user's configuration.

    Also scans ~/.gemini/extensions/ for extension-defined MCP servers.
    """

    def __init__(self, config_path: Optional[Path] = None):
        self.config_path = config_path or Path.home() / ".gemini" / "settings.json"
        self.extensions_dir = self.config_path.parent / "extensions"
        self.extension_enablement_path = self.extensions_dir / "extension-enablement.json"

    def _ensure_dir_exists(self) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)

    def _load_json(self) -> Dict[str, Any]:
        """Load the settings.json file."""
        if not self.config_path.exists():
            return {}
        try:
            return json.loads(self.config_path.read_text())
        except Exception as e:
            logger.error(f"Failed to load gemini config: {e}")
            return {}

    def _save_json(self, data: Dict[str, Any]) -> None:
        """Save the settings.json file."""
        self._ensure_dir_exists()
        self.config_path.write_text(json.dumps(data, indent=2) + "\n")

    def _load_extension_enablement(self) -> Dict[str, Any]:
        """Load the extension-enablement.json file."""
        if not self.extension_enablement_path.exists():
            return {}
        try:
            return json.loads(self.extension_enablement_path.read_text())
        except Exception as e:
            logger.warning(f"Failed to load extension enablement: {e}")
            return {}

    def _scan_extensions(self) -> List[MCPServer]:
        """Scan ~/.gemini/extensions/ for extension-defined MCP servers.

        Each extension can have a gemini-extension.json that defines MCP servers:
        {
            "name": "extension-name",
            "mcpServers": {
                "server-name": {
                    "command": "node",
                    "args": ["${extensionPath}/mcp-server/dist/index.js"],
                    "env": {}
                }
            }
        }

        Returns:
            List of MCPServer objects from enabled extensions.
        """
        if not self.extensions_dir.exists():
            return []

        enablement = self._load_extension_enablement()
        servers = []

        for ext_dir in self.extensions_dir.iterdir():
            if not ext_dir.is_dir():
                continue

            ext_name = ext_dir.name
            ext_json_path = ext_dir / "gemini-extension.json"

            if not ext_json_path.exists():
                continue

            # Check if extension is enabled (extensions are enabled by default)
            # If enablement entry exists, it's enabled; if it has "disabled": true, it's disabled
            ext_enablement = enablement.get(ext_name, {})
            if isinstance(ext_enablement, dict) and ext_enablement.get("disabled", False):
                logger.debug(f"Skipping disabled extension: {ext_name}")
                continue

            try:
                ext_config = json.loads(ext_json_path.read_text())
                mcp_servers_config = ext_config.get("mcpServers", {})

                for server_name, server_config in mcp_servers_config.items():
                    if not isinstance(server_config, dict):
                        continue

                    # Resolve ${extensionPath} placeholder in command and args
                    command = server_config.get("command", "")
                    args = server_config.get("args", [])
                    env = server_config.get("env")

                    # Replace ${extensionPath} with actual path
                    ext_path_str = str(ext_dir)
                    command = command.replace("${extensionPath}", ext_path_str)
                    args = [arg.replace("${extensionPath}", ext_path_str) for arg in args]

                    servers.append(MCPServer(
                        name=server_name,
                        command=command,
                        args=args,
                        env=env if env else None,
                    ))
                    logger.debug(f"Found extension MCP server: {server_name} from {ext_name}")

            except Exception as e:
                logger.warning(f"Failed to load extension {ext_name}: {e}")
                continue

        return servers

    def list_extensions(self) -> List[Dict[str, Any]]:
        """List all installed Gemini CLI extensions.

        Returns:
            List of extension info dicts with:
            - name: Extension name
            - version: Extension version
            - path: Extension directory path
            - enabled: Whether the extension is enabled
            - mcp_servers: List of MCP server names defined by this extension
            - description: Extension description (if available)
        """
        if not self.extensions_dir.exists():
            return []

        enablement = self._load_extension_enablement()
        extensions = []

        for ext_dir in self.extensions_dir.iterdir():
            if not ext_dir.is_dir():
                continue

            ext_name = ext_dir.name
            ext_json_path = ext_dir / "gemini-extension.json"

            if not ext_json_path.exists():
                continue

            try:
                ext_config = json.loads(ext_json_path.read_text())

                # Check enablement
                ext_enablement = enablement.get(ext_name, {})
                is_enabled = True
                if isinstance(ext_enablement, dict) and ext_enablement.get("disabled", False):
                    is_enabled = False

                # Get MCP server names
                mcp_server_names = list(ext_config.get("mcpServers", {}).keys())

                extensions.append({
                    "name": ext_config.get("name", ext_name),
                    "version": ext_config.get("version", "unknown"),
                    "path": str(ext_dir),
                    "enabled": is_enabled,
                    "mcp_servers": mcp_server_names,
                    "description": ext_config.get("description", ""),
                })

            except Exception as e:
                logger.warning(f"Failed to read extension {ext_name}: {e}")
                continue

        return sorted(extensions, key=lambda x: x["name"])

    def load_config(self) -> CLIConfig:
        """Load the configuration including MCP servers from settings.json and extensions."""
        data = self._load_json()
        shinka_config = data.get("shinka", {})

        # Parse MCP servers from settings.json - Gemini uses 'mcpServers' key
        mcp_servers = []
        mcp_data = data.get("mcpServers", {})
        for name, server_config in mcp_data.items():
            if isinstance(server_config, dict):
                mcp_servers.append(MCPServer(
                    name=name,
                    command=server_config.get("command", ""),
                    args=server_config.get("args", []),
                    env=server_config.get("env"),
                ))

        # Also scan extensions for MCP servers
        extension_servers = self._scan_extensions()
        # Merge extension servers, avoiding duplicates (settings.json takes precedence)
        existing_names = {s.name for s in mcp_servers}
        for ext_server in extension_servers:
            if ext_server.name not in existing_names:
                mcp_servers.append(ext_server)
                existing_names.add(ext_server.name)

        return CLIConfig(
            system_prompt=shinka_config.get("system_prompt"),
            mcp_servers=mcp_servers,
            allowed_tools=None,  # Gemini doesn't have tool restrictions
            extra_config={k: v for k, v in shinka_config.items() if k != "system_prompt"},
        )

    def save_config(self, config: CLIConfig) -> None:
        """Save the configuration."""
        data = self._load_json()

        # Save shinka-specific config
        shinka_config = {}
        if config.system_prompt:
            shinka_config["system_prompt"] = config.system_prompt
        shinka_config.update(config.extra_config)
        data["shinka"] = shinka_config

        # Save MCP servers
        mcp_servers = {}
        for server in config.mcp_servers:
            server_config = {
                "command": server.command,
                "args": server.args,
            }
            if server.env:
                server_config["env"] = server.env
            mcp_servers[server.name] = server_config
        data["mcpServers"] = mcp_servers

        self._save_json(data)
        logger.info(f"Saved Gemini config to {self.config_path}")

    def add_mcp_server(self, server: MCPServer) -> None:
        """Add or update an MCP server."""
        config = self.load_config()
        # Remove existing server with same name
        config.mcp_servers = [s for s in config.mcp_servers if s.name != server.name]
        config.mcp_servers.append(server)
        self.save_config(config)

    def remove_mcp_server(self, name: str) -> bool:
        """Remove an MCP server by name. Returns True if removed."""
        config = self.load_config()
        original_len = len(config.mcp_servers)
        config.mcp_servers = [s for s in config.mcp_servers if s.name != name]
        if len(config.mcp_servers) < original_len:
            self.save_config(config)
            return True
        return False

    def list_system_prompts(self) -> List[Dict[str, Any]]:
        """List available system prompt files in ~/.gemini/.

        Returns:
            List of system prompt dicts with 'name', 'path', and 'size'.
        """
        gemini_dir = self.config_path.parent
        prompts = []

        if not gemini_dir.exists():
            return prompts

        for md_file in gemini_dir.glob("*.md"):
            # Skip context files like GEMINI.md and AGENTS.md
            if md_file.name.upper() in ("GEMINI.MD", "AGENTS.MD"):
                continue
            try:
                stat = md_file.stat()
                prompts.append({
                    "name": md_file.name,
                    "path": str(md_file),
                    "size": stat.st_size,
                    "modified": stat.st_mtime,
                })
            except Exception:
                pass

        # Sort by name
        prompts.sort(key=lambda p: p["name"])
        return prompts

    def create_system_prompt(self, name: str, content: str) -> str:
        """Create a new system prompt file.

        Args:
            name: Filename (will add .md if not present)
            content: System prompt content

        Returns:
            Path to the created file.
        """
        if not name.endswith(".md"):
            name += ".md"

        gemini_dir = self.config_path.parent
        gemini_dir.mkdir(parents=True, exist_ok=True)

        file_path = gemini_dir / name
        file_path.write_text(content)
        logger.info(f"Created system prompt file: {file_path}")
        return str(file_path)


class ClaudeConfigManager:
    """Manages ~/.claude.json configuration.

    Claude Code reads MCP servers from ~/.claude.json (NOT ~/.claude/settings.json).
    Uses JSON format with MCP servers in 'mcpServers' key.
    Also supports 'allowedTools' for restricting available tools.
    """

    def __init__(self, config_path: Optional[Path] = None):
        self.config_path = config_path or Path.home() / ".claude.json"

    def _ensure_dir_exists(self) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)

    def _load_json(self) -> Dict[str, Any]:
        """Load the settings.json file."""
        if not self.config_path.exists():
            return {}
        try:
            return json.loads(self.config_path.read_text())
        except Exception as e:
            logger.error(f"Failed to load claude config: {e}")
            return {}

    def _save_json(self, data: Dict[str, Any]) -> None:
        """Save the settings.json file."""
        self._ensure_dir_exists()
        self.config_path.write_text(json.dumps(data, indent=2) + "\n")

    def load_config(self) -> CLIConfig:
        """Load the configuration."""
        data = self._load_json()
        shinka_config = data.get("shinka", {})

        # Parse MCP servers - Claude uses 'mcpServers' key
        mcp_servers = []
        mcp_data = data.get("mcpServers", {})
        for name, server_config in mcp_data.items():
            if isinstance(server_config, dict):
                mcp_servers.append(MCPServer(
                    name=name,
                    command=server_config.get("command", ""),
                    args=server_config.get("args", []),
                    env=server_config.get("env"),
                ))

        # Parse allowed tools
        allowed_tools = shinka_config.get("allowed_tools")

        return CLIConfig(
            system_prompt=shinka_config.get("system_prompt"),
            mcp_servers=mcp_servers,
            allowed_tools=allowed_tools,
            extra_config={k: v for k, v in shinka_config.items()
                         if k not in ("system_prompt", "allowed_tools")},
        )

    def save_config(self, config: CLIConfig) -> None:
        """Save the configuration."""
        data = self._load_json()

        # Save shinka-specific config
        shinka_config = {}
        if config.system_prompt:
            shinka_config["system_prompt"] = config.system_prompt
        if config.allowed_tools:
            shinka_config["allowed_tools"] = config.allowed_tools
        shinka_config.update(config.extra_config)
        data["shinka"] = shinka_config

        # Save MCP servers
        mcp_servers = {}
        for server in config.mcp_servers:
            server_config = {
                "command": server.command,
                "args": server.args,
            }
            if server.env:
                server_config["env"] = server.env
            mcp_servers[server.name] = server_config
        data["mcpServers"] = mcp_servers

        self._save_json(data)
        logger.info(f"Saved Claude config to {self.config_path}")

    def add_mcp_server(self, server: MCPServer) -> None:
        """Add or update an MCP server."""
        config = self.load_config()
        config.mcp_servers = [s for s in config.mcp_servers if s.name != server.name]
        config.mcp_servers.append(server)
        self.save_config(config)

    def remove_mcp_server(self, name: str) -> bool:
        """Remove an MCP server by name. Returns True if removed."""
        config = self.load_config()
        original_len = len(config.mcp_servers)
        config.mcp_servers = [s for s in config.mcp_servers if s.name != name]
        if len(config.mcp_servers) < original_len:
            self.save_config(config)
            return True
        return False


@dataclass
class JulesConfig:
    """Jules-specific configuration stored in ~/.shinka/jules_config.json.

    Jules is a cloud-based agent that operates on GitHub repos via REST API.
    These settings are used by the WebUI to populate extra_cli_config.
    """
    github_repo: str = ""
    base_branch: str = "main"
    automation_mode: str = "AUTO_CREATE_PR"  # AUTO_CREATE_PR or ""
    poll_interval: int = 15
    cleanup_branch: bool = True


class JulesConfigManager:
    """Manages Jules configuration in ~/.shinka/jules_config.json.

    Jules is a cloud-based agent (not a CLI) so it doesn't have local CLI config.
    We store WebUI preferences for GitHub repo, branch, and other settings.
    """

    def __init__(self, config_path: Optional[Path] = None):
        self.config_path = config_path or Path.home() / ".shinka" / "jules_config.json"

    def _ensure_dir_exists(self) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)

    def load_config(self) -> JulesConfig:
        """Load Jules configuration."""
        if not self.config_path.exists():
            return JulesConfig()
        try:
            data = json.loads(self.config_path.read_text())
            return JulesConfig(**{k: v for k, v in data.items() if k in JulesConfig.__dataclass_fields__})
        except Exception as e:
            logger.error(f"Failed to load Jules config: {e}")
            return JulesConfig()

    def save_config(self, config: JulesConfig) -> None:
        """Save Jules configuration."""
        self._ensure_dir_exists()
        self.config_path.write_text(json.dumps(asdict(config), indent=2) + "\n")
        logger.info(f"Saved Jules config to {self.config_path}")

    def to_extra_cli_config(self, config: Optional[JulesConfig] = None) -> Dict[str, Any]:
        """Convert JulesConfig to extra_cli_config dict for agentic runner.

        Args:
            config: JulesConfig to convert. If None, loads from disk.

        Returns:
            Dict suitable for AgenticConfig.extra_cli_config
        """
        if config is None:
            config = self.load_config()
        return {
            "github_repo": config.github_repo,
            "base_branch": config.base_branch,
            "automation_mode": config.automation_mode,
            "poll_interval": config.poll_interval,
            "cleanup_branch": config.cleanup_branch,
        }


# Factory function for getting the right manager
def get_cli_config_manager(provider: str):
    """Get the appropriate config manager for a CLI provider.

    Args:
        provider: One of 'codex', 'gemini', 'claude', 'jules', 'shinka'

    Returns:
        The appropriate config manager instance.

    Raises:
        ValueError: If provider is not recognized.
    """
    managers = {
        "codex": CodexProfileManager,
        "gemini": GeminiConfigManager,
        "claude": ClaudeConfigManager,
        "jules": JulesConfigManager,
    }

    if provider == "shinka":
        # ShinkaAgent doesn't use external CLI config
        return None

    if provider not in managers:
        raise ValueError(f"Unknown CLI provider: {provider}")

    return managers[provider]()


class SelectedProfilesManager:
    """Manages selected profiles for Shinka runs.

    Stores which profile/system-prompt is selected for each provider
    in ~/.shinka/selected_profiles.json.
    """

    def __init__(self, config_path: Optional[Path] = None):
        self.config_path = config_path or Path.home() / ".shinka" / "selected_profiles.json"

    def _ensure_dir_exists(self) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> Dict[str, Any]:
        if not self.config_path.exists():
            return {}
        try:
            return json.loads(self.config_path.read_text())
        except Exception as e:
            logger.error(f"Failed to load selected profiles: {e}")
            return {}

    def _save(self, data: Dict[str, Any]) -> None:
        self._ensure_dir_exists()
        self.config_path.write_text(json.dumps(data, indent=2) + "\n")

    def get_selected(self, provider: str) -> Dict[str, Any]:
        """Get the selected profile/config for a provider.

        Returns dict with:
        - codex: {"profile": "default", "sandbox": "workspace-write", "model_reasoning_effort": "high"}
        - gemini: {"system_prompt_file": "my_custom.md", "sandbox_disabled": true}
        - claude: {"skip_permissions": true}
        """
        data = self._load()
        return data.get(provider, {})

    def set_selected(self, provider: str, selection: Dict[str, Any]) -> None:
        """Set the selected profile/config for a provider."""
        data = self._load()
        data[provider] = selection
        self._save(data)
        logger.info(f"Updated selected profile for {provider}: {selection}")

    def get_all(self) -> Dict[str, Any]:
        """Get all selected profiles."""
        return self._load()


# Singleton instance for selected profiles
_selected_profiles_manager: Optional[SelectedProfilesManager] = None


def get_selected_profiles_manager() -> SelectedProfilesManager:
    """Get the singleton SelectedProfilesManager instance."""
    global _selected_profiles_manager
    if _selected_profiles_manager is None:
        _selected_profiles_manager = SelectedProfilesManager()
    return _selected_profiles_manager
