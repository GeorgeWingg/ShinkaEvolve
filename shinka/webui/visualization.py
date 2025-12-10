#!/usr/bin/env python3
"""
Shinka Visualization Module

This module provides visualization capabilities for Shinka evolution results.
It serves a web interface for exploring evolution databases and meta files.
"""

import argparse
import base64
import http.server
import json
import markdown
import os
import re
import socketserver
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import webbrowser
from pathlib import Path
from typing import Optional, Dict, Any, Tuple

from shinka.database import DatabaseConfig, ProgramDatabase
from shinka.tools.codex_session_registry import list_session_processes
from shinka.webui.credential_checker import CredentialChecker
from shinka.webui.presets import PresetManager
from shinka.webui.git_worktree import GitWorktreeManager
from shinka.webui.run_config import UIRunConfig, RunConfigBuilder, flatten_nested_config
from shinka.webui.cli_profiles import (
    get_cli_config_manager,
    get_selected_profiles_manager,
    CLIConfig,
    MCPServer,
    CodexProfileManager,
    GeminiConfigManager,
)

# We'll use a simple text-to-PDF approach instead of complex dependencies
WEASYPRINT_AVAILABLE = False

DEFAULT_PORT = 8000
CACHE_EXPIRATION_SECONDS = 5  # Cache data for 5 seconds
db_cache: Dict[str, Tuple[float, Any]] = {}

# Large metadata fields to exclude from initial /get_programs response
# These can contain MB of data (session transcripts, command outputs, etc.)
# and are only needed when viewing a specific program's details
LARGE_METADATA_FIELDS = {
    "agent_session_events",  # Full session event log (can be 100s of MB)
    "agent_commands",  # Command objects with full stdout/stderr
    "agent_session_log",  # Session transcript text
    "agent_changed_files",  # Full file contents (can be 100s of MB)
    "agent_code_diffs",  # Full diffs for all changed files (can be 100s of MB)
    "agent_binary_files",  # Binary file data (can be 100s of MB)
    "agent_workspace_snapshot",  # Full workspace state
    "events_preview",  # Preview of events (still can be large)
    "commands_run",  # Evaluator commands with full output
    "stdout_log",  # Concatenated stdout
    "stderr_log",  # Concatenated stderr
    "embedding_corpus_meta",  # Embedding corpus metadata (can be large)
}

# Fields inside nested dicts (like agentic_evaluator) to also filter
LARGE_NESTED_FIELDS = {
    "agentic_evaluator": {"commands_run", "events_preview", "stdout_log", "stderr_log"},
}


def filter_large_metadata(metadata: Dict[str, Any]) -> Dict[str, Any]:
    """
    Filter out large metadata fields that aren't needed for tree visualization.
    Returns a shallow copy with large fields replaced by size indicators.
    """
    if not metadata:
        return metadata

    filtered = {}
    for key, value in metadata.items():
        if key in LARGE_METADATA_FIELDS:
            # Replace with size indicator so frontend knows data exists
            if isinstance(value, (list, str)):
                filtered[f"_{key}_size"] = len(value)
            elif isinstance(value, dict):
                filtered[f"_{key}_size"] = len(json.dumps(value)) if value else 0
            # Don't include the actual large value
        elif key in LARGE_NESTED_FIELDS and isinstance(value, dict):
            # Filter nested dict (e.g., agentic_evaluator)
            nested_filtered = {}
            for nested_key, nested_value in value.items():
                if nested_key in LARGE_NESTED_FIELDS[key]:
                    if isinstance(nested_value, (list, str)):
                        nested_filtered[f"_{nested_key}_size"] = len(nested_value)
                    elif isinstance(nested_value, dict):
                        nested_filtered[f"_{nested_key}_size"] = len(json.dumps(nested_value)) if nested_value else 0
                else:
                    nested_filtered[nested_key] = nested_value
            filtered[key] = nested_filtered
        else:
            filtered[key] = value

    return filtered

# Track additional results directories from UI-launched runs (outside the server's results/ dir)
# This allows the UI to find databases in external workspaces
launched_run_roots: set = set()


class DatabaseRequestHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, search_root=None, **kwargs):
        self.search_root = search_root or os.getcwd()
        super().__init__(*args, **kwargs)

    def log_message(self, format, *args):
        """Override to provide more detailed logging."""
        print(f"\n[SERVER] {format % args}")

    def do_GET(self):
        print(f"\n[SERVER] Received GET request for: {self.path}")
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path
        query = urllib.parse.parse_qs(parsed_url.query)

        if path == "/list_databases":
            return self.handle_list_databases()

        if path == "/get_programs" and "db_path" in query:
            db_path = query["db_path"][0]
            return self.handle_get_programs(db_path)

        if path == "/get_program_details" and "db_path" in query and "program_id" in query:
            db_path = query["db_path"][0]
            program_id = query["program_id"][0]
            return self.handle_get_program_details(db_path, program_id)

        if path == "/get_meta_files" and "db_path" in query:
            db_path = query["db_path"][0]
            return self.handle_get_meta_files(db_path)

        if path == "/get_meta_content" and "db_path" in query and "generation" in query:
            db_path = query["db_path"][0]
            generation = query["generation"][0]
            return self.handle_get_meta_content(db_path, generation)

        if (
            path == "/download_meta_pdf"
            and "db_path" in query
            and "generation" in query
        ):
            db_path = query["db_path"][0]
            generation = query["generation"][0]
            return self.handle_download_meta_pdf(db_path, generation)

        if path == "/api/backend_bandit":
            return self.handle_backend_bandit_status()

        if path == "/api/gemini_status":
            return self.handle_gemini_status()

        if path == "/api/claude_status":
            return self.handle_claude_status()

        if path == "/api/codex_usage":
            return self.handle_codex_usage()

        if path == "/api/gemini_usage":
            return self.handle_gemini_usage()

        if path == "/api/credentials":
            return self.handle_credentials_get()

        if path == "/api/evolution_runs":
            return self.handle_evolution_runs()

        if path == "/api/active_jobs" and "db_path" in query:
            return self.handle_active_jobs(query)

        if path == "/api/session_state" and "session_id" in query:
            return self.handle_session_state(query)

        # New Run feature endpoints
        if path == "/api/credentials/check":
            return self.handle_credentials_check()

        if path == "/api/presets":
            return self.handle_presets_list()

        if path.startswith("/api/presets/") and len(path.split("/")) == 4:
            preset_id = path.split("/")[3]
            return self.handle_preset_get(preset_id)

        if path == "/api/config_defaults":
            return self.handle_config_defaults()

        if path == "/api/browse_folders":
            return self.handle_browse_folders(query)

        if path == "/api/folder_picker_native":
            return self.handle_folder_picker_native(query)

        # CLI Config endpoints
        if path.startswith("/api/cli_config/"):
            parts = path.split("/")
            if len(parts) >= 4:
                provider = parts[3]
                if len(parts) == 4:
                    # GET /api/cli_config/{provider}
                    return self.handle_cli_config_get(provider)
                elif len(parts) == 5 and parts[4] == "mcp":
                    # GET /api/cli_config/{provider}/mcp
                    return self.handle_cli_config_mcp_list(provider)
                elif len(parts) == 5 and parts[4] == "profiles":
                    # GET /api/cli_config/{provider}/profiles
                    return self.handle_cli_config_profiles_list(provider)

        # Workspace diff and patch export endpoints
        if path == "/api/workspace_diff" and "db_path" in query:
            return self.handle_workspace_diff(query)

        if path == "/api/export_patch" and "db_path" in query and "program_id" in query:
            return self.handle_export_patch(query)

        if path == "/":
            print("[SERVER] Root path requested, serving viz_tree.html")
            self.path = "/viz_tree.html"

        # Serve static files from the webui directory
        return http.server.SimpleHTTPRequestHandler.do_GET(self)

    def do_POST(self):
        """Handle POST requests."""
        print(f"\n[SERVER] Received POST request for: {self.path}")
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path

        if path == "/api/evolution_run/delete":
            return self.handle_evolution_run_delete()

        if path == "/api/evolution_run/stop":
            return self.handle_evolution_run_stop()

        if path == "/api/credentials":
            return self.handle_credentials_post()

        # New Run feature endpoints
        if path == "/api/evolution_run/start":
            return self.handle_evolution_run_start()

        if path == "/api/evolution_run/validate":
            return self.handle_evolution_run_validate()

        if path == "/api/evolution_run/export":
            return self.handle_evolution_run_export()

        if path == "/api/presets":
            return self.handle_preset_save()

        if path.startswith("/api/presets/") and len(path.split("/")) == 4:
            preset_id = path.split("/")[3]
            return self.handle_preset_delete(preset_id)

        if path == "/api/git/prepare":
            return self.handle_git_prepare()

        # CLI Config endpoints
        if path.startswith("/api/cli_config/"):
            parts = path.split("/")
            if len(parts) >= 4:
                provider = parts[3]
                if len(parts) == 4:
                    # POST /api/cli_config/{provider}
                    return self.handle_cli_config_save(provider)
                elif len(parts) == 5 and parts[4] == "mcp":
                    # POST /api/cli_config/{provider}/mcp (add MCP server)
                    return self.handle_cli_config_mcp_add(provider)
                elif len(parts) == 6 and parts[4] == "mcp":
                    # POST /api/cli_config/{provider}/mcp/{name} (delete MCP server)
                    mcp_name = parts[5]
                    return self.handle_cli_config_mcp_delete(provider, mcp_name)
                elif len(parts) == 5 and parts[4] == "profiles":
                    # POST /api/cli_config/{provider}/profiles (update selected profile)
                    return self.handle_cli_config_profiles_update(provider)

        # Return 404 for unknown POST endpoints
        self.send_error(404, f"Unknown POST endpoint: {path}")

    def handle_evolution_run_delete(self):
        """Delete an evolution run directory."""
        import shutil
        import signal
        import subprocess

        try:
            # Read the request body
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            data = json.loads(body.decode("utf-8"))

            run_id = data.get("run_id")
            run_dir = data.get("run_dir")

            print(f"[SERVER] Delete request for run_id={run_id}, run_dir={run_dir}")

            if not run_dir:
                self.send_json_response({"ok": False, "error": "No run_dir provided"})
                return

            # Resolve the path
            run_path = os.path.abspath(run_dir)
            search_root = os.path.abspath(self.search_root)

            # Security check: ensure path is under search_root OR in launched_run_roots
            is_allowed = run_path.startswith(search_root)
            if not is_allowed:
                # Check if it's from an external workspace we launched
                for external_root in launched_run_roots:
                    if run_path.startswith(os.path.abspath(external_root)) or run_path == os.path.abspath(external_root):
                        is_allowed = True
                        break
                    # Also check parent dirs (run_path might be the tracked root itself)
                    if os.path.abspath(external_root).startswith(run_path):
                        is_allowed = True
                        break

            if not is_allowed:
                self.send_json_response({
                    "ok": False,
                    "error": "Path is outside the allowed directory"
                })
                return

            # Check if the directory exists
            if not os.path.isdir(run_path):
                self.send_json_response({
                    "ok": False,
                    "error": f"Directory not found: {run_path}"
                })
                return

            # Kill any running processes for this run
            # Search for processes with this run's results_dir in the command line
            try:
                result = subprocess.run(
                    ["pgrep", "-f", run_path],
                    capture_output=True,
                    text=True
                )
                if result.stdout.strip():
                    pids = result.stdout.strip().split('\n')
                    for pid in pids:
                        try:
                            pid_int = int(pid)
                            print(f"[SERVER] Killing process {pid_int} for run {run_id}")
                            os.kill(pid_int, signal.SIGTERM)
                        except (ValueError, ProcessLookupError, PermissionError) as e:
                            print(f"[SERVER] Could not kill PID {pid}: {e}")
            except Exception as e:
                print(f"[SERVER] Error checking for running processes: {e}")

            # Also check shinka.pid file
            pid_file = os.path.join(run_path, "shinka.pid")
            if os.path.exists(pid_file):
                try:
                    with open(pid_file, "r") as f:
                        pid = int(f.read().strip())
                    os.kill(pid, signal.SIGTERM)
                    print(f"[SERVER] Killed process from shinka.pid: {pid}")
                except (ValueError, ProcessLookupError, PermissionError):
                    pass

            # Give processes time to die
            import time
            time.sleep(0.5)

            # Delete the directory
            print(f"[SERVER] Deleting directory: {run_path}")
            shutil.rmtree(run_path)

            # Remove from launched_run_roots if present
            launched_run_roots.discard(run_path)

            self.send_json_response({"ok": True})
            print(f"[SERVER] Successfully deleted: {run_path}")

        except json.JSONDecodeError as e:
            self.send_json_response({"ok": False, "error": f"Invalid JSON: {e}"})
        except Exception as e:
            print(f"[SERVER] Error deleting run: {e}")
            self.send_json_response({"ok": False, "error": str(e)})

    def handle_evolution_run_stop(self):
        """Stop a running evolution without deleting the directory."""
        import signal
        import subprocess

        try:
            # Read the request body
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            data = json.loads(body.decode("utf-8"))

            run_id = data.get("run_id")
            run_dir = data.get("run_dir")

            print(f"[SERVER] Stop request for run_id={run_id}, run_dir={run_dir}")

            if not run_dir:
                self.send_json_response({"ok": False, "error": "No run_dir provided"})
                return

            # Resolve the path
            run_path = os.path.abspath(run_dir)
            killed_count = 0

            # Kill any running processes for this run
            # Search for processes with this run's results_dir in the command line
            try:
                result = subprocess.run(
                    ["pgrep", "-f", run_path],
                    capture_output=True,
                    text=True
                )
                if result.stdout.strip():
                    pids = result.stdout.strip().split('\n')
                    for pid in pids:
                        try:
                            pid_int = int(pid)
                            # Don't kill ourselves
                            if pid_int == os.getpid():
                                continue
                            print(f"[SERVER] Killing process {pid_int} for run {run_id}")
                            os.kill(pid_int, signal.SIGTERM)
                            killed_count += 1
                        except (ValueError, ProcessLookupError, PermissionError) as e:
                            print(f"[SERVER] Could not kill PID {pid}: {e}")
            except Exception as e:
                print(f"[SERVER] Error checking for running processes: {e}")

            # Also check shinka.pid file
            pid_file = os.path.join(run_path, "shinka.pid")
            if os.path.exists(pid_file):
                try:
                    with open(pid_file, "r") as f:
                        pid = int(f.read().strip())
                    if pid != os.getpid():
                        os.kill(pid, signal.SIGTERM)
                        print(f"[SERVER] Killed process from shinka.pid: {pid}")
                        killed_count += 1
                except (ValueError, ProcessLookupError, PermissionError) as e:
                    print(f"[SERVER] Could not kill PID from shinka.pid: {e}")

            self.send_json_response({
                "ok": True,
                "killed_processes": killed_count,
                "message": f"Stopped {killed_count} process(es)" if killed_count > 0 else "No running processes found"
            })
            print(f"[SERVER] Successfully stopped run: {run_path} (killed {killed_count} processes)")

        except json.JSONDecodeError as e:
            self.send_json_response({"ok": False, "error": f"Invalid JSON: {e}"})
        except Exception as e:
            print(f"[SERVER] Error stopping run: {e}")
            self.send_json_response({"ok": False, "error": str(e)})

    def handle_backend_bandit_status(self):
        """Return backend auth status and bandit summary (if available)."""
        print("[SERVER] Received request for backend bandit status")
        try:
            from shinka.tools.auth_status import get_authenticated_backends_summary
            summary = get_authenticated_backends_summary()
            response = {
                "available_backends": summary.get("available", []),
                "unavailable_backends": summary.get("unavailable", []),
                "backends": summary.get("details", {}),
                "bandit_active": False,  # Static endpoint; runner would set this
            }
            self.send_json_response(response)
        except Exception as e:
            print(f"[SERVER] Error getting backend bandit status: {e}")
            self.send_json_response({
                "available_backends": [],
                "unavailable_backends": ["codex", "gemini", "claude", "shinka"],
                "backends": {},
                "bandit_active": False,
                "error": str(e),
            })

    def handle_gemini_status(self):
        """Return Gemini CLI auth status."""
        print("[SERVER] Received request for Gemini status")
        try:
            from shinka.tools.auth_status import check_gemini_auth
            status = check_gemini_auth()
            self.send_json_response({
                "available": status.cli_path is not None,
                "authenticated": status.available,
                "plan": status.plan,
                "error": status.error,
            })
        except Exception as e:
            print(f"[SERVER] Error getting Gemini status: {e}")
            self.send_json_response({
                "available": False,
                "authenticated": False,
                "error": str(e),
            })

    def handle_gemini_usage(self):
        """Return Gemini usage and auth status (parity with Codex)."""
        print("[SERVER] Received request for Gemini usage")
        try:
            from shinka.tools.gemini_usage import collect_usage, GeminiUsageError
            from shinka.tools.auth_status import check_gemini_auth

            # First check auth status
            auth_status = check_gemini_auth()
            if not auth_status.available:
                self.send_json_response({
                    "authenticated": False,
                    "error": auth_status.error or "Not authenticated",
                })
                return

            # Try to get usage data
            try:
                usage = collect_usage()
                self.send_json_response({
                    "authenticated": True,
                    "plan": usage.plan,
                    "email": usage.email,
                    "windows": [
                        {
                            "label": w.label,
                            "percent_used": w.percent_used,
                            "window_minutes": w.window_minutes,
                            "reset_at": w.reset_at,
                            "reset_at_local": w.reset_at_local,
                        }
                        for w in usage.windows
                    ],
                })
            except GeminiUsageError as e:
                # Auth file exists but usage fetch failed
                self.send_json_response({
                    "authenticated": True,
                    "plan": auth_status.plan,
                    "email": auth_status.email,
                    "error": str(e),
                    "windows": [],
                })
        except Exception as e:
            print(f"[SERVER] Error getting Gemini usage: {e}")
            self.send_json_response({
                "authenticated": False,
                "error": str(e),
            })

    def handle_claude_status(self):
        """Return Claude CLI auth status."""
        print("[SERVER] Received request for Claude status")
        try:
            from shinka.tools.auth_status import check_claude_auth
            status = check_claude_auth()
            self.send_json_response({
                "available": status.cli_path is not None,
                "authenticated": status.available,
                "plan": status.plan,
                "error": status.error,
            })
        except Exception as e:
            print(f"[SERVER] Error getting Claude status: {e}")
            self.send_json_response({
                "available": False,
                "authenticated": False,
                "error": str(e),
            })

    def handle_codex_usage(self):
        """Return Codex usage and auth status."""
        print("[SERVER] Received request for Codex usage")
        try:
            from shinka.tools.codex_usage import collect_usage, CodexUsageError
            from shinka.tools.auth_status import check_codex_auth
            
            # First check auth status
            auth_status = check_codex_auth()
            if not auth_status.available:
                self.send_json_response({
                    "authenticated": False,
                    "error": auth_status.error or "Not authenticated",
                })
                return
            
            # Try to get usage data
            try:
                usage = collect_usage()
                self.send_json_response({
                    "authenticated": True,
                    "plan": usage.plan,
                    "email": usage.email,
                    "windows": [
                        {
                            "label": w.label,
                            "percent_used": w.percent_used,
                            "window_minutes": w.window_minutes,
                            "reset_at": w.reset_at,
                            "reset_at_local": w.reset_at_local,
                        }
                        for w in usage.windows
                    ],
                })
            except CodexUsageError as e:
                # Auth file exists but usage fetch failed
                self.send_json_response({
                    "authenticated": True,
                    "plan": auth_status.plan,
                    "error": str(e),
                    "windows": [],
                })
        except Exception as e:
            print(f"[SERVER] Error getting Codex usage: {e}")
            self.send_json_response({
                "authenticated": False,
                "error": str(e),
            })

    def handle_credentials_get(self):
        """Return list of configured providers and their status (no actual keys exposed)."""
        print("[SERVER] Received GET request for credentials status")
        try:
            from shinka.tools.credentials import (
                load_credentials_store,
                get_api_key,
                ENV_VAR_MAP,
            )
            
            # Map frontend provider names to backend provider names
            FRONTEND_TO_BACKEND = {
                "openai": "codex",
                "anthropic": "claude",
                "google": "gemini",
                "deepseek": "deepseek",
                "openrouter": "openrouter",
                "azure": "azure",
            }
            
            # Additional env vars for providers not in credentials.py
            EXTRA_ENV_VARS = {
                "deepseek": "DEEPSEEK_API_KEY",
                "openrouter": "OPENROUTER_API_KEY",
                "azure": "AZURE_OPENAI_API_KEY",
            }
            
            store = load_credentials_store()
            providers_status = {}
            
            # Check each provider
            for frontend_name, backend_name in FRONTEND_TO_BACKEND.items():
                has_key = False
                source = None
                
                # Check credential store
                if backend_name in store and store[backend_name]:
                    has_key = True
                    source = "store"
                else:
                    # Check env vars
                    env_var = ENV_VAR_MAP.get(backend_name) or EXTRA_ENV_VARS.get(backend_name)
                    if env_var and os.environ.get(env_var):
                        has_key = True
                        source = "env"
                
                providers_status[frontend_name] = {
                    "configured": has_key,
                    "source": source,
                }
            
            self.send_json_response({
                "ok": True,
                "providers": providers_status,
            })
        except Exception as e:
            print(f"[SERVER] Error getting credentials status: {e}")
            self.send_json_response({
                "ok": False,
                "error": str(e),
            })

    def handle_credentials_post(self):
        """Save or delete an API key to the credential store."""
        print("[SERVER] Received POST request to save credentials")
        try:
            from shinka.tools.credentials import set_api_key, remove_api_key
            
            # Map frontend provider names to backend provider names
            FRONTEND_TO_BACKEND = {
                "openai": "codex",
                "anthropic": "claude", 
                "google": "gemini",
                "deepseek": "deepseek",
                "openrouter": "openrouter",
                "azure": "azure",
            }
            
            # Read the request body
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            data = json.loads(body.decode("utf-8"))
            
            provider = data.get("provider", "").lower()
            api_key = data.get("api_key", "")
            action = data.get("action", "set")  # "set" or "delete"
            
            if not provider:
                self.send_json_response({"ok": False, "error": "No provider specified"})
                return
            
            # Map frontend name to backend name
            backend_provider = FRONTEND_TO_BACKEND.get(provider, provider)
            
            if action == "delete":
                remove_api_key(backend_provider)
                print(f"[SERVER] Removed API key for provider: {backend_provider}")
                self.send_json_response({"ok": True, "action": "deleted"})
            else:
                if not api_key:
                    self.send_json_response({"ok": False, "error": "No API key provided"})
                    return
                    
                set_api_key(backend_provider, api_key)
                print(f"[SERVER] Saved API key for provider: {backend_provider}")
                
                # Also set it in the environment for immediate use
                # This ensures ShinkaAgent can see it without restart
                env_var_map = {
                    "codex": "OPENAI_API_KEY",
                    "gemini": "GEMINI_API_KEY", 
                    "claude": "ANTHROPIC_API_KEY",
                    "deepseek": "DEEPSEEK_API_KEY",
                    "openrouter": "OPENROUTER_API_KEY",
                    "azure": "AZURE_OPENAI_API_KEY",
                }
                env_var = env_var_map.get(backend_provider)
                if env_var:
                    os.environ[env_var] = api_key
                    print(f"[SERVER] Also set {env_var} in environment")
                
                self.send_json_response({"ok": True, "action": "saved"})
                
        except json.JSONDecodeError as e:
            self.send_json_response({"ok": False, "error": f"Invalid JSON: {e}"})
        except Exception as e:
            print(f"[SERVER] Error saving credentials: {e}")
            import traceback
            traceback.print_exc()
            self.send_json_response({"ok": False, "error": str(e)})

    def handle_evolution_runs(self):
        """Return list of evolution runs by scanning the results directory."""
        print("[SERVER] Received request for evolution runs")
        try:
            runs = []
            now = time.time()
            
            # Scan search_root for task directories, then run directories inside each
            # Structure: search_root/<task>/<run>/evolution_db.sqlite
            if os.path.exists(self.search_root):
                for task_name in os.listdir(self.search_root):
                    task_dir = os.path.join(self.search_root, task_name)
                    if not os.path.isdir(task_dir):
                        continue
                    
                    # Scan each task directory for run directories
                    for run_name in os.listdir(task_dir):
                        run_dir = os.path.join(task_dir, run_name)
                        if not os.path.isdir(run_dir):
                            continue
                        
                        db_path = os.path.join(run_dir, "evolution_db.sqlite")
                        if not os.path.exists(db_path):
                            continue
                        
                        # This is a valid run directory
                        run_info = {
                            "run_id": run_name,
                            "run_name": run_name,
                            "run_dir": run_dir,
                            "task": task_name,
                            "status": "completed",
                            "agent_type": "Unknown",
                            "generations": 0,
                            "start_time": None,
                            "duration": None,
                            "pid": None,
                        }
                        
                        # Check if there's a PID file indicating a running process
                        pid_path = os.path.join(run_dir, "shinka.pid")
                        if os.path.exists(pid_path):
                            try:
                                with open(pid_path, 'r') as f:
                                    pid = int(f.read().strip())
                                    run_info["pid"] = pid
                                    
                                    # Check if process is still running
                                    try:
                                        os.kill(pid, 0)  # Signal 0 just checks if process exists
                                        run_info["status"] = "running"
                                    except (OSError, ProcessLookupError):
                                        # Process is not running
                                        pass
                            except (ValueError, IOError):
                                pass
                        
                        # Get generation count and agent type from database
                        try:
                            conn = sqlite3.connect(db_path)
                            cursor = conn.cursor()
                            
                            # Get max generation
                            cursor.execute("SELECT MAX(generation) FROM programs")
                            max_gen = cursor.fetchone()[0]
                            if max_gen is not None:
                                run_info["generations"] = max_gen + 1
                            
                            # Try to get agent type from metadata of a non-gen-0 program
                            cursor.execute("""
                                SELECT metadata FROM programs 
                                WHERE generation > 0 AND metadata IS NOT NULL 
                                LIMIT 1
                            """)
                            row = cursor.fetchone()
                            if row and row[0]:
                                try:
                                    meta = json.loads(row[0])
                                    backend = meta.get("agent_backend") or meta.get("patch_type", "Unknown")
                                    run_info["agent_type"] = backend
                                except:
                                    pass
                            
                            conn.close()
                        except Exception as e:
                            print(f"[SERVER] Error reading DB {db_path}: {e}")
                        
                        # Get start time from directory mtime or hydra config
                        try:
                            hydra_config = os.path.join(run_dir, ".hydra", "config.yaml")
                            if os.path.exists(hydra_config):
                                run_info["start_time"] = os.path.getmtime(hydra_config)
                            else:
                                run_info["start_time"] = os.path.getmtime(run_dir)
                        except:
                            pass
                        
                        # Calculate duration
                        if run_info["start_time"]:
                            if run_info["status"] == "running":
                                # Live duration
                                elapsed = now - run_info["start_time"]
                            else:
                                # Use last modification time of db
                                try:
                                    db_mtime = os.path.getmtime(db_path)
                                    elapsed = db_mtime - run_info["start_time"]
                                except:
                                    elapsed = 0
                            
                            hrs = int(elapsed // 3600)
                            mins = int((elapsed % 3600) // 60)
                            if hrs > 0:
                                run_info["duration"] = f"{hrs}h {mins}m"
                            else:
                                run_info["duration"] = f"{mins}m"
                        
                        # For running jobs, also get active sessions
                        if run_info["status"] == "running":
                            run_info["active_sessions"] = self._get_active_sessions_for_run(run_dir, now)
                        else:
                            run_info["active_sessions"] = []
                        
                        runs.append(run_info)
            
            # Also scan launched run directories (external workspaces)
            for external_run_dir in launched_run_roots:
                db_path = os.path.join(external_run_dir, "evolution_db.sqlite")
                if not os.path.exists(db_path):
                    continue

                # Extract task name and run name from path
                # Path format: /tmp/ui_test2/results/shinka_ui_test2/ui-test-noembedding
                parts = external_run_dir.rstrip("/").split("/")
                run_name = parts[-1] if parts else "unknown"
                task_name = parts[-2] if len(parts) >= 2 else "external"

                run_info = {
                    "run_id": run_name,
                    "run_name": run_name,
                    "run_dir": external_run_dir,
                    "task": task_name,
                    "status": "completed",
                    "agent_type": "Unknown",
                    "generations": 0,
                    "start_time": None,
                    "duration": None,
                    "pid": None,
                    "is_external": True,  # Mark as external workspace
                }

                # Check PID file
                pid_path = os.path.join(external_run_dir, "shinka.pid")
                if os.path.exists(pid_path):
                    try:
                        with open(pid_path, 'r') as f:
                            pid = int(f.read().strip())
                            run_info["pid"] = pid
                            try:
                                os.kill(pid, 0)
                                run_info["status"] = "running"
                            except (OSError, ProcessLookupError):
                                pass
                    except (ValueError, IOError):
                        pass

                # Get DB info
                try:
                    conn = sqlite3.connect(db_path)
                    cursor = conn.cursor()
                    cursor.execute("SELECT MAX(generation) FROM programs")
                    max_gen = cursor.fetchone()[0]
                    if max_gen is not None:
                        run_info["generations"] = max_gen + 1
                    cursor.execute("""
                        SELECT metadata FROM programs
                        WHERE generation > 0 AND metadata IS NOT NULL LIMIT 1
                    """)
                    row = cursor.fetchone()
                    if row and row[0]:
                        try:
                            meta = json.loads(row[0])
                            run_info["agent_type"] = meta.get("agent_backend") or meta.get("patch_type", "Unknown")
                        except:
                            pass
                    conn.close()
                except Exception as e:
                    print(f"[SERVER] Error reading external DB {db_path}: {e}")

                # Get start time
                try:
                    hydra_config = os.path.join(external_run_dir, ".hydra", "config.yaml")
                    if os.path.exists(hydra_config):
                        run_info["start_time"] = os.path.getmtime(hydra_config)
                    else:
                        run_info["start_time"] = os.path.getmtime(external_run_dir)
                except:
                    pass

                # Duration
                if run_info["start_time"]:
                    if run_info["status"] == "running":
                        elapsed = now - run_info["start_time"]
                    else:
                        try:
                            elapsed = os.path.getmtime(db_path) - run_info["start_time"]
                        except:
                            elapsed = 0
                    hrs = int(elapsed // 3600)
                    mins = int((elapsed % 3600) // 60)
                    run_info["duration"] = f"{hrs}h {mins}m" if hrs > 0 else f"{mins}m"

                if run_info["status"] == "running":
                    run_info["active_sessions"] = self._get_active_sessions_for_run(external_run_dir, now)
                else:
                    run_info["active_sessions"] = []

                runs.append(run_info)

            # Sort by start time, newest first
            runs.sort(key=lambda r: r.get("start_time") or 0, reverse=True)

            print(f"[SERVER] Found {len(runs)} evolution runs")
            self.send_json_response({"runs": runs})
            
        except Exception as e:
            print(f"[SERVER] Error listing evolution runs: {e}")
            import traceback
            traceback.print_exc()
            self.send_json_response({"runs": [], "error": str(e)})

    def handle_active_jobs(self, query: Dict[str, Any]):
        """Return list of currently active/in-progress evolution jobs.
        
        Uses the session registry with PID-based verification for 100% accuracy.
        A job is active if and only if its CLI process is still running.
        """
        db_path = query.get("db_path", [""])[0]
        if not db_path:
            return self.send_json_response({"jobs": []})

        try:
            # Resolve the db_path to get the run directory (handles external workspaces)
            abs_db_path = self._resolve_db_path(db_path)
            run_dir = os.path.dirname(abs_db_path)
            run_dir_normalized = os.path.normpath(run_dir)

            active_jobs = []
            now = time.time()

            # PRIMARY SOURCE: Session registry with PID-based verification
            # list_session_processes() already verifies PIDs are alive and cleans up dead entries
            registry_sessions = list_session_processes()
            
            for session in registry_sessions:
                workdir = session.get("workdir", "")
                # Check if this session belongs to the requested run
                # The workdir could be in /tmp/shinka_scratch/uuid or in run_dir/agent_sessions/uuid
                results_dir = session.get("results_dir")  # Try registry first
                session_path = Path(workdir)
                
                # Fallback: Try to find results_dir from session_meta.json in the workdir
                if not results_dir:
                    meta_path = session_path / "session_meta.json"
                    if meta_path.exists():
                        try:
                            with open(meta_path, 'r') as mf:
                                meta = json.load(mf)
                                results_dir = meta.get("results_dir", "")
                        except Exception:
                            pass
                
                # Match session to this run
                belongs_to_run = False
                if results_dir:
                    # Normalize the results_dir (resolve relative to workspace root)
                    if os.path.isabs(results_dir):
                        results_dir_abs = os.path.normpath(results_dir)
                    else:
                        # The session's results_dir is relative to workspace root,
                        # but self.search_root might already include part of the path.
                        # Find the workspace root by going up from search_root
                        workspace_root = os.path.dirname(self.search_root)
                        if os.path.basename(self.search_root) == "results":
                            # search_root is the 'results' directory
                            results_dir_abs = os.path.normpath(os.path.join(workspace_root, results_dir))
                        else:
                            results_dir_abs = os.path.normpath(os.path.join(self.search_root, results_dir))
                    
                    if results_dir_abs == run_dir_normalized:
                        belongs_to_run = True
                elif run_dir_normalized in workdir:
                    # Fallback: workdir contains the run_dir path
                    belongs_to_run = True
                
                if belongs_to_run:
                    session_kind = session.get("session_kind", "unknown")
                    session_type = "eval" if "eval" in session_kind.lower() else "edit"
                    
                    active_jobs.append({
                        "session_id": os.path.basename(workdir),
                        "session_type": session_type,
                        "phase": session_type,
                        "pid": session.get("pid"),
                        "started_at": session.get("started_at", 0),
                        "parent_id": session.get("parent_id"),
                        "generation": session.get("generation"),
                        "patch_type": session.get("patch_type"),
                        "status": session.get("status", "running"),
                        "can_stop": session.get("can_stop", False),
                        "scratch_path": workdir if "/tmp/shinka_scratch" in workdir else None,
                    })

            if active_jobs:
                print(f"[SERVER] Found {len(active_jobs)} active jobs (PID-verified)")
            
            self.send_json_response({"jobs": active_jobs})
        except Exception as e:
            print(f"[SERVER] Error getting active jobs: {e}")
            import traceback
            traceback.print_exc()
            self.send_json_response({"jobs": [], "error": str(e)})

    def handle_session_state(self, query: Dict[str, Any]):
        """Return current state of an active session for in-progress node display.
        
        This endpoint reads session_meta.json and session_log.jsonl to provide
        the same data structure expected by the UI tabs (LLM Result, Evaluation, etc.)
        """
        session_id = query.get("session_id", [""])[0]
        if not session_id:
            return self.send_json_response({"error": "No session_id provided"})

        try:
            # Get active sessions from registry
            registry_sessions = list_session_processes()
            
            # Find the session by ID - check multiple possible matches:
            # 1. The workdir basename (UUID) matches session_id
            # 2. The session_id field in registry matches
            # 3. The workdir path ends with session_id
            session_info = None
            for session in registry_sessions:
                workdir = session.get("workdir", "")
                reg_session_id = session.get("session_id", "")
                workdir_basename = os.path.basename(workdir)
                
                if (workdir_basename == session_id or 
                    reg_session_id == session_id or 
                    workdir.endswith(session_id)):
                    session_info = session
                    break
            
            if not session_info:
                return self.send_json_response({
                    "error": "Session not found",
                    "status": "not_found"
                })
            
            workdir = session_info.get("workdir", "")
            meta_path = os.path.join(workdir, "session_meta.json")
            log_path = os.path.join(workdir, "session_log.jsonl")
            
            # Read session metadata
            meta = {}
            if os.path.exists(meta_path):
                with open(meta_path, 'r') as f:
                    meta = json.load(f)
            
            # Merge registry info with meta
            meta.update({
                "pid": session_info.get("pid"),
                "status": session_info.get("status", "running"),
                "started_at": session_info.get("started_at"),
                "session_kind": session_info.get("session_kind"),
            })
            
            # Read session events from log
            events = []
            if os.path.exists(log_path):
                with open(log_path, 'r') as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            try:
                                events.append(json.loads(line))
                            except json.JSONDecodeError:
                                pass
            
            # Parse events into structured data for UI tabs
            parsed = self._parse_session_events(events)
            
            self.send_json_response({
                "meta": meta,
                "events": events,
                "parsed": parsed,
                "status": "running",
            })
            
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.send_json_response({"error": str(e), "status": "error"})

    def _parse_session_events(self, events: list) -> dict:
        """Parse session events into structured data for UI display.
        
        Returns a dict with:
        - timeline: List of events for the LLM Result tab (agentic timeline)
        - commands: List of command executions
        - messages: List of agent messages
        - usage: Token usage statistics
        """
        timeline = []
        commands = []
        messages = []
        usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "total_cost_usd": 0}
        session_id = None
        model = None
        
        for event in events:
            event_type = event.get("type")
            
            if event_type == "init":
                session_id = event.get("session_id")
                model = event.get("model")
                timeline.append({
                    "type": "init",
                    "timestamp": event.get("timestamp"),
                    "model": model,
                    "session_id": session_id,
                })
            
            elif event_type == "tool_use":
                timeline.append({
                    "type": "tool_use",
                    "timestamp": event.get("timestamp"),
                    "tool_name": event.get("tool_name"),
                    "tool_id": event.get("tool_id"),
                    "parameters": event.get("parameters", {}),
                })
            
            elif event_type == "command_execution":
                item = event.get("item", {})
                cmd_info = {
                    "command": item.get("command"),
                    "status": item.get("status"),
                    "exit_code": item.get("exit_code"),
                    "stdout": item.get("stdout", ""),
                    "stderr": item.get("stderr", ""),
                }
                commands.append(cmd_info)
                timeline.append({
                    "type": "command_result",
                    "timestamp": event.get("timestamp"),
                    **cmd_info,
                })
            
            elif event_type == "agent_message":
                item = event.get("item", {})
                text = item.get("text", "")
                messages.append(text)
                timeline.append({
                    "type": "message",
                    "timestamp": event.get("timestamp"),
                    "text": text,
                })
            
            elif event_type == "usage":
                usage_data = event.get("usage", {})
                usage["input_tokens"] += usage_data.get("input_tokens", 0)
                usage["output_tokens"] += usage_data.get("output_tokens", 0)
                usage["total_tokens"] += usage_data.get("total_tokens", 0)
                usage["total_cost_usd"] += usage_data.get("total_cost_usd", 0)
        
        return {
            "timeline": timeline,
            "commands": commands,
            "messages": messages,
            "usage": usage,
            "session_id": session_id,
            "model": model,
        }

    def _get_active_sessions_for_run(self, run_dir: str, now: float) -> list:
        """Get list of active sessions for a specific run directory."""
        active_sessions = []
        tracked_job_ids = set()
        run_dir_abs = os.path.abspath(run_dir)
        
        # PRIMARY SOURCE: Session registry (uses PID-based liveness checking)
        # This is the most reliable source for detecting active sessions
        try:
            registry_sessions = list_session_processes()
            for session in registry_sessions:
                results_dir = session.get("results_dir", "")
                if not results_dir:
                    continue
                
                # Check if this session belongs to this run
                # Handle both relative and absolute paths
                results_dir_abs = os.path.abspath(results_dir)
                matches = (
                    results_dir_abs == run_dir_abs or
                    run_dir_abs.endswith(results_dir.lstrip('./'))
                )
                
                if matches:
                    session_id = session.get("session_id", "")
                    workdir = session.get("workdir", "")
                    # Extract UUID from workdir path (only if it's a scratch directory)
                    workdir_session_id = ""
                    if workdir and "/shinka_scratch/" in workdir:
                        workdir_session_id = os.path.basename(workdir)
                    generation = session.get("generation")
                    
                    # Use session_id from registry, or fall back to workdir UUID
                    final_session_id = session_id or workdir_session_id or str(session.get("pid", ""))
                    
                    session_info = {
                        "session_id": final_session_id,
                        "workdir_session_id": workdir_session_id,  # UUID from workdir path
                        "session_type": session.get("session_kind", "edit"),
                        "generation": generation,
                        "parent_id": session.get("parent_id"),
                        "patch_type": session.get("patch_type"),
                        "pid": session.get("pid"),
                        "started_at": session.get("started_at"),
                        "age_seconds": int(now - session.get("started_at", now)) if session.get("started_at") else 0,
                        "current_action": f"Running Gen {generation} (agentic)" if generation is not None else "Evaluating...",
                    }
                    
                    # Try to get more specific action from the session log
                    if workdir:
                        log_path = os.path.join(workdir, "session_log.jsonl")
                        if os.path.exists(log_path):
                            try:
                                with open(log_path, 'r') as lf:
                                    lines = lf.readlines()
                                    if lines:
                                        last_line = lines[-1].strip()
                                        if last_line:
                                            event = json.loads(last_line)
                                            event_type = event.get("type", "")
                                            if event_type == "tool_use":
                                                tool_name = event.get("tool_name", "tool")
                                                session_info["current_action"] = f"Using {tool_name}"
                                            elif event_type == "assistant":
                                                session_info["current_action"] = "Thinking..."
                                            elif event_type in ("command", "command_execution"):
                                                item = event.get("item", {})
                                                cmd = item.get("command", event.get("command", ""))[:30]
                                                session_info["current_action"] = f"Running: {cmd}..." if cmd else f"Running Gen {generation}"
                            except:
                                pass
                    
                    active_sessions.append(session_info)
                    tracked_job_ids.add(session_id)
                    tracked_job_ids.add(workdir_session_id)
        except Exception as e:
            print(f"[SERVER] Error reading session registry: {e}")
        
        # SECONDARY SOURCE: Check active_jobs.json (maintained by launcher)
        active_jobs_path = os.path.join(run_dir, "active_jobs.json")
        if os.path.exists(active_jobs_path):
            try:
                with open(active_jobs_path, 'r') as f:
                    active_jobs = json.load(f)
                    for job in active_jobs:
                        job_id = job.get("job_id", "")
                        generation = job.get("generation")
                        parent_id = job.get("parent_id")
                        start_time = job.get("start_time", 0)
                        
                        # Determine session type from job_id
                        if "eval" in job_id.lower():
                            session_type = "eval"
                        else:
                            session_type = "edit"
                        
                        session_info = {
                            "session_id": job_id,
                            "session_type": session_type,
                            "generation": generation,
                            "parent_id": parent_id,
                            "start_time": start_time,
                            "age_seconds": int(now - start_time) if start_time else 0,
                            "current_action": f"Running Gen {generation} (agentic)",
                        }
                        active_sessions.append(session_info)
                        tracked_job_ids.add(job_id)
            except Exception as e:
                print(f"[SERVER] Error reading active_jobs.json: {e}")
        
        # SECONDARY SOURCE: Scan for recently modified session logs
        # This is a FALLBACK for sessions not tracked in the registry (e.g., legacy runs).
        # Note: Time-based detection is unreliable for long-running sessions that block
        # on slow commands - prefer the PID-based registry check above.
        # Use different time windows: edit sessions can be long, eval sessions are quicker
        session_dirs = [
            (os.path.join(run_dir, "agent_sessions"), "edit", 120),  # 2 min window
            (os.path.join(run_dir, "agentic_eval_sessions"), "eval", 90),  # 1.5 min window
        ]
        
        for sessions_dir, session_type, time_window in session_dirs:
            if not os.path.exists(sessions_dir):
                continue
            
            try:
                for session_id in os.listdir(sessions_dir):
                    # Skip if already tracked via active_jobs.json
                    if session_id in tracked_job_ids:
                        continue
                    
                    session_path = os.path.join(sessions_dir, session_id)
                    if not os.path.isdir(session_path):
                        continue
                    
                    log_path = os.path.join(session_path, "session_log.jsonl")
                    if not os.path.exists(log_path):
                        continue
                    
                    try:
                        stat = os.stat(log_path)
                        # Check if modified within the time window for this session type
                        if now - stat.st_mtime < time_window:
                            session_info = {
                                "session_id": session_id,
                                "session_type": session_type,
                                "last_modified": stat.st_mtime,
                                "age_seconds": int(now - stat.st_mtime),
                            }
                            
                            # Try to get metadata
                            meta_path = os.path.join(session_path, "session_meta.json")
                            if os.path.exists(meta_path):
                                try:
                                    with open(meta_path, 'r') as mf:
                                        meta = json.load(mf)
                                        session_info["generation"] = meta.get("generation")
                                        session_info["parent_id"] = meta.get("parent_id")
                                        session_info["patch_type"] = meta.get("patch_type")
                                        session_info["backend"] = meta.get("backend")
                                except:
                                    pass
                            
                            # Get last few events from log
                            try:
                                with open(log_path, 'r') as lf:
                                    lines = lf.readlines()
                                    if lines:
                                        last_line = lines[-1].strip()
                                        if last_line:
                                            event = json.loads(last_line)
                                            session_info["last_event_type"] = event.get("type", "unknown")
                                            # Get a brief summary of what's happening
                                            event_type = event.get("type", "")
                                            if event_type == "tool_use":
                                                tool = event.get("tool", {})
                                                session_info["current_action"] = f"Using {tool.get('name', 'tool')}"
                                            elif event_type == "assistant":
                                                session_info["current_action"] = "Thinking..."
                                            elif event_type in ("command", "command_execution"):
                                                item = event.get("item", {})
                                                cmd = item.get("command", event.get("command", ""))[:30]
                                                session_info["current_action"] = f"Running: {cmd}..." if cmd else "Running command..."
                                            elif event_type == "agent_message":
                                                # For eval sessions, show evaluating status
                                                if session_type == "eval":
                                                    session_info["current_action"] = "Evaluating..."
                                                else:
                                                    session_info["current_action"] = "Generating..."
                                            else:
                                                # Default based on session type
                                                if session_type == "eval":
                                                    session_info["current_action"] = "Evaluating..."
                                                else:
                                                    session_info["current_action"] = event_type or "Working..."
                            except:
                                # Default action based on session type
                                if session_type == "eval":
                                    session_info["current_action"] = "Evaluating..."
                                else:
                                    session_info["current_action"] = "Working..."
                            
                            active_sessions.append(session_info)
                            tracked_job_ids.add(session_id)
                    except Exception:
                        pass
            except Exception:
                pass
        
        # Also check /tmp/shinka_scratch for sessions that belong to this run
        scratch_dir = "/tmp/shinka_scratch"
        if os.path.exists(scratch_dir):
            try:
                for session_id in os.listdir(scratch_dir):
                    session_path = os.path.join(scratch_dir, session_id)
                    if not os.path.isdir(session_path):
                        continue
                    
                    meta_path = os.path.join(session_path, "session_meta.json")
                    if os.path.exists(meta_path):
                        try:
                            with open(meta_path, 'r') as mf:
                                meta = json.load(mf)
                                results_dir = meta.get("results_dir", "")
                                # Check if this session belongs to this run
                                # Handle both relative and absolute paths
                                if results_dir:
                                    # Normalize the results_dir - if relative, it's relative to workspace
                                    results_dir_abs = os.path.abspath(results_dir)
                                    run_dir_abs = os.path.abspath(run_dir)
                                    # Also check if run_dir ends with results_dir (for relative path matching)
                                    matches = (
                                        results_dir_abs == run_dir_abs or
                                        run_dir_abs.endswith(results_dir.lstrip('./'))
                                    )
                                    if matches:
                                        log_path = os.path.join(session_path, "session_log.jsonl")
                                        if os.path.exists(log_path):
                                            stat = os.stat(log_path)
                                            if now - stat.st_mtime < 120:  # 2 min window for scratch
                                                # Don't duplicate if already tracked
                                                if not any(s["session_id"] == session_id for s in active_sessions):
                                                    generation = meta.get("generation")
                                                    session_info = {
                                                        "session_id": session_id,
                                                        "session_type": "scratch",
                                                        "last_modified": stat.st_mtime,
                                                        "age_seconds": int(now - stat.st_mtime),
                                                        "generation": generation,
                                                        "parent_id": meta.get("parent_id"),
                                                        "patch_type": meta.get("patch_type"),
                                                        "backend": meta.get("backend"),
                                                        "current_action": f"Running Gen {generation} (agentic)" if generation else "Running (agentic)",
                                                    }
                                                    # Try to get more specific action from log
                                                    try:
                                                        with open(log_path, 'r') as lf:
                                                            lines = lf.readlines()
                                                            if lines:
                                                                last_line = lines[-1].strip()
                                                                if last_line:
                                                                    event = json.loads(last_line)
                                                                    event_type = event.get("type", "")
                                                                    if event_type == "tool_use":
                                                                        tool = event.get("tool", {})
                                                                        session_info["current_action"] = f"Using {tool.get('name', 'tool')}"
                                                                    elif event_type == "assistant":
                                                                        session_info["current_action"] = "Thinking..."
                                                                    elif event_type in ("command", "command_execution"):
                                                                        item = event.get("item", {})
                                                                        cmd = item.get("command", event.get("command", ""))[:30]
                                                                        session_info["current_action"] = f"Running: {cmd}..." if cmd else f"Running Gen {generation}"
                                                    except:
                                                        pass
                                                    active_sessions.append(session_info)
                        except:
                            pass
            except:
                pass

        return active_sessions

    def handle_list_databases(self):
        """Scan the search root directory and launched run directories for .db files."""
        print(
            f"[SERVER] Received request for database list, "
            f"searching in: {self.search_root}"
        )
        db_files = []
        date_pattern = re.compile(r"(\d{4}\.\d{2}\.\d{2}\d{6})|_(\d{8}_\d{6})")

        # Get the task name from the search root directory name
        task_name = os.path.basename(self.search_root)

        # Helper to scan a directory and add databases
        def scan_directory(search_dir: str, is_external: bool = False):
            if not os.path.exists(search_dir):
                return
            print(f"[SERVER] Scanning for .db files in: {search_dir}")
            for root, _, files in os.walk(search_dir):
                for f in files:
                    if f.lower().endswith((".db", ".sqlite")):
                        full_path = os.path.join(root, f)

                        if is_external:
                            # For external workspaces, use @external: prefix with absolute path
                            client_path = f"@external:{full_path}"
                            # Extract workspace name and run name for display
                            parts = full_path.split("/")
                            if len(parts) >= 2:
                                display_name = f"{parts[-3]}/{parts[-2]}" if len(parts) >= 3 else parts[-2]
                            else:
                                display_name = Path(full_path).parent.name
                        else:
                            client_path = os.path.relpath(full_path, search_dir)
                            display_name = f"{Path(f).stem} - {Path(client_path).parent}"

                        # Extract date for sorting
                        sort_key = "0"  # Default for paths without a date
                        match = date_pattern.search(client_path)
                        if match:
                            sort_key = match.group(1) or match.group(2)

                        # Modify the path structure to include task name for proper organization
                        if not is_external:
                            path_parts = client_path.split("/")
                            if len(path_parts) < 3:
                                modified_client_path = f"{task_name}/{client_path}"
                            else:
                                modified_client_path = client_path
                        else:
                            modified_client_path = client_path

                        db_info = {
                            "path": modified_client_path,
                            "name": display_name,
                            "sort_key": sort_key,
                            "actual_path": client_path if not is_external else full_path,
                        }
                        db_files.append(db_info)
                        print(
                            f"[SERVER] Found DB: {client_path} -> {modified_client_path} (sort: {sort_key})"
                        )

        # Scan main search root
        scan_directory(self.search_root, is_external=False)

        # Also scan any launched run directories (external workspaces)
        for external_root in launched_run_roots:
            scan_directory(external_root, is_external=True)

        if not db_files:
            print("[SERVER] No database files found in search directory.")

        # Sort databases by the extracted date, newest first
        db_files.sort(key=lambda x: x.get("sort_key", "0"), reverse=True)

        # Remove sort_key before sending to client (but keep actual_path)
        for db in db_files:
            del db["sort_key"]

        self.send_json_response(db_files)
        print(f"[SERVER] Served DB list with {len(db_files)} entries, sorted by date.")

    def _get_actual_db_path(self, db_path: str) -> str:
        """Convert a potentially modified db_path back to the actual file path.

        Returns a tuple of (is_absolute, path) where is_absolute indicates if
        the path is an absolute path (from external workspace) or relative to search_root.
        """
        # Handle external workspace paths (absolute paths)
        if db_path.startswith("@external:"):
            return db_path[len("@external:"):]

        task_name = os.path.basename(self.search_root)

        # If the path starts with the task name, remove it
        if db_path.startswith(f"{task_name}/"):
            return db_path[len(task_name) + 1 :]

        return db_path

    def _resolve_db_path(self, db_path: str) -> str:
        """Resolve a db_path to an absolute file path."""
        actual_path = self._get_actual_db_path(db_path)

        # If it's already an absolute path (from external workspace), use it directly
        if os.path.isabs(actual_path):
            return actual_path

        # Otherwise, it's relative to search_root
        return os.path.join(self.search_root, actual_path)

    def handle_get_programs(self, db_path: str):
        """Fetch all programs from a given database file."""
        print(f"[SERVER] Fetching programs from DB: {db_path}")

        # Check cache first
        if db_path in db_cache:
            last_fetch_time, cached_data = db_cache[db_path]
            if time.time() - last_fetch_time < CACHE_EXPIRATION_SECONDS:
                print(f"[SERVER] Serving from cache for DB: {db_path}")
                self.send_json_response(cached_data)
                return

        # Resolve the db_path to an absolute path (handles both local and external workspaces)
        abs_db_path = self._resolve_db_path(db_path)
        print(f"[SERVER] Absolute DB path: {abs_db_path} (from {db_path})")

        if not os.path.exists(abs_db_path):
            self.send_error(404, f"Database file not found: {abs_db_path}")
            return

        # Retry logic for the reader with improved WAL mode support
        max_retries = 5  # Increased retries for better resilience
        delay = 0.1  # Shorter initial delay
        for i in range(max_retries):
            db = None
            try:
                config = DatabaseConfig(db_path=abs_db_path)
                db = ProgramDatabase(config, read_only=True)

                # Set WAL mode compatible settings for read-only connections
                if db.cursor:
                    db.cursor.execute(
                        "PRAGMA busy_timeout = 10000;"
                    )  # 10 second timeout
                    db.cursor.execute("PRAGMA journal_mode = WAL;")  # Ensure WAL mode

                programs = db.get_all_programs()

                # Convert Program objects to dicts for JSON
                # Filter out large metadata fields to reduce payload size
                # (full metadata available via /get_program_details endpoint)
                programs_dict = []
                for p in programs:
                    p_dict = p.to_dict()
                    if p_dict.get("metadata"):
                        p_dict["metadata"] = filter_large_metadata(p_dict["metadata"])
                    programs_dict.append(p_dict)

                # Update cache (with filtered data)
                db_cache[db_path] = (time.time(), programs_dict)

                self.send_json_response(programs_dict)
                success_msg = (
                    f"[SERVER] Successfully served {len(programs)} "
                    f"programs from {db_path} (attempt {i + 1})"
                )
                print(success_msg)
                return  # Success, exit the retry loop

            except (sqlite3.OperationalError, sqlite3.DatabaseError) as e:
                error_str = str(e).lower()
                if "database is locked" in error_str or "busy" in error_str:
                    print(
                        f"[SERVER] Attempt {i + 1}/{max_retries} - database busy, "
                        f"retrying in {delay:.1f}s... ({e})"
                    )
                    if i < max_retries - 1:
                        time.sleep(delay)
                        delay = min(delay * 1.5, 2.0)  # Exponential backoff, max 2s
                        continue
                else:
                    print(f"[SERVER] Non-recoverable database error: {e}")
                    self.send_error(500, f"Database error: {str(e)}")
                    return

                # Last retry failed
                if i == max_retries - 1:
                    err_msg = (
                        f"[SERVER] Database still busy after {max_retries} attempts"
                    )
                    print(err_msg)
                    self.send_error(
                        503,
                        "Database temporarily unavailable - evolution may be running",
                    )

            except Exception as e:
                # Catch any other unexpected errors
                print(f"[SERVER] An unexpected error occurred: {e}")
                self.send_error(500, f"An unexpected error occurred: {str(e)}")
                return  # Don't retry on unknown errors
            finally:
                # Ensure database connection is properly closed
                if db and hasattr(db, "close"):
                    try:
                        db.close()
                    except Exception as e:
                        print(f"[SERVER] Warning: Error closing database: {e}")

    def handle_get_program_details(self, db_path: str, program_id: str):
        """Fetch full details for a single program, including all metadata.

        This endpoint returns unfiltered data for viewing program details,
        agent session transcripts, command outputs, etc.
        """
        print(f"[SERVER] Fetching program details: {program_id} from {db_path}")

        abs_db_path = self._resolve_db_path(db_path)
        if not os.path.exists(abs_db_path):
            self.send_error(404, f"Database file not found: {abs_db_path}")
            return

        db = None
        try:
            config = DatabaseConfig(db_path=abs_db_path)
            db = ProgramDatabase(config, read_only=True)

            if db.cursor:
                db.cursor.execute("PRAGMA busy_timeout = 10000;")
                db.cursor.execute("PRAGMA journal_mode = WAL;")

            # Query for single program by ID
            db.cursor.execute(
                """
                SELECT p.*,
                       CASE WHEN a.program_id IS NOT NULL THEN 1 ELSE 0 END as in_archive
                FROM programs p
                LEFT JOIN archive a ON p.id = a.program_id
                WHERE p.id = ?
                """,
                (program_id,)
            )
            row = db.cursor.fetchone()
            if row is None:
                self.send_error(404, f"Program not found: {program_id}")
                return

            program = db._program_from_row(row)
            if program is None:
                self.send_error(500, f"Failed to parse program: {program_id}")
                return

            # Return full unfiltered program data
            program_dict = program.to_dict()
            self.send_json_response(program_dict)
            print(f"[SERVER] Successfully served program details for {program_id}")

        except (sqlite3.OperationalError, sqlite3.DatabaseError) as e:
            print(f"[SERVER] Database error fetching program details: {e}")
            self.send_error(500, f"Database error: {str(e)}")
        except Exception as e:
            print(f"[SERVER] Error fetching program details: {e}")
            self.send_error(500, f"Error: {str(e)}")
        finally:
            if db and hasattr(db, "close"):
                try:
                    db.close()
                except Exception as e:
                    print(f"[SERVER] Warning: Error closing database: {e}")

    def handle_get_meta_files(self, db_path: str):
        """List available meta_{gen}.txt files for a given database."""
        print(f"[SERVER] Listing meta files for DB: {db_path}")

        # Resolve the db_path to an absolute path (handles external workspaces)
        abs_db_path = self._resolve_db_path(db_path)
        db_dir = os.path.dirname(abs_db_path)

        if not os.path.exists(db_dir):
            self.send_error(404, f"Database directory not found: {db_dir}")
            return

        meta_files = []
        try:
            # Look for meta_{gen}.txt files in the same directory as the DB
            for file in os.listdir(db_dir):
                if file.startswith("meta_") and file.endswith(".txt"):
                    # Extract generation number
                    gen_str = file[5:-4]  # Remove 'meta_' and '.txt'
                    try:
                        generation = int(gen_str)
                        meta_files.append(
                            {
                                "generation": generation,
                                "filename": file,
                                "path": os.path.join(db_dir, file),
                            }
                        )
                    except ValueError:
                        # Skip files that don't have valid generation numbers
                        continue

            # Sort by generation number
            meta_files.sort(key=lambda x: x["generation"])

            print(f"[SERVER] Found {len(meta_files)} meta files")
            self.send_json_response(meta_files)

        except Exception as e:
            print(f"[SERVER] Error listing meta files: {e}")
            self.send_error(500, f"Error listing meta files: {str(e)}")

    def handle_get_meta_content(self, db_path: str, generation: str):
        """Get the content of a specific meta_{gen}.txt file."""
        print(
            f"[SERVER] Fetching meta content for DB: {db_path}, "
            f"generation: {generation}"
        )

        # Resolve the db_path to an absolute path (handles external workspaces)
        abs_db_path = self._resolve_db_path(db_path)
        db_dir = os.path.dirname(abs_db_path)

        # Construct the meta file path
        meta_filename = f"meta_{generation}.txt"
        meta_file_path = os.path.join(db_dir, meta_filename)

        if not os.path.exists(meta_file_path):
            self.send_error(404, f"Meta file not found: {meta_filename}")
            return

        try:
            with open(meta_file_path, "r", encoding="utf-8") as f:
                content = f.read()

            response_data = {
                "generation": int(generation),
                "filename": meta_filename,
                "content": content,
            }

            print(
                f"[SERVER] Successfully served meta content for generation {generation}"
            )
            self.send_json_response(response_data)

        except Exception as e:
            print(f"[SERVER] Error reading meta file: {e}")
            self.send_error(500, f"Error reading meta file: {str(e)}")

    def handle_download_meta_pdf(self, db_path: str, generation: str):
        """Convert a specific meta_{gen}.txt file to PDF and serve it."""
        print(
            f"[SERVER] PDF download request for DB: {db_path}, generation: {generation}"
        )

        # Resolve the db_path to an absolute path (handles external workspaces)
        abs_db_path = self._resolve_db_path(db_path)
        db_dir = os.path.dirname(abs_db_path)

        # Construct the meta file path
        meta_filename = f"meta_{generation}.txt"
        meta_file_path = os.path.join(db_dir, meta_filename)

        if not os.path.exists(meta_file_path):
            self.send_error(404, f"Meta file not found: {meta_filename}")
            return

        try:
            with open(meta_file_path, "r", encoding="utf-8") as f:
                content = f.read()

            pdf_filename = f"meta_{generation}.pdf"

            # Try to generate PDF using available methods
            pdf_bytes = self._generate_pdf(content, generation)

            if pdf_bytes is None:
                print("[SERVER] All PDF generation methods failed, serving text")
                # Fall back to serving formatted text with PDF headers
                formatted_content = (
                    f"Meta Generation {generation}\n{'=' * 50}\n\n{content}"
                )
                pdf_bytes = formatted_content.encode("utf-8")

            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header(
                "Content-Disposition", f'attachment; filename="{pdf_filename}"'
            )
            self.send_header("Content-Length", str(len(pdf_bytes)))
            self.end_headers()
            self.wfile.write(pdf_bytes)
            print(f"[SERVER] Successfully served PDF: {pdf_filename}")

        except Exception as e:
            print(f"[SERVER] Error converting meta file to PDF: {e}")
            self.send_error(500, f"Error converting to PDF: {str(e)}")

    def _generate_pdf(self, content: str, generation: str) -> bytes:
        """Generate PDF from markdown content using available methods."""

        print(f"[SERVER] Attempting to generate PDF for generation {generation}")

        # Method 1: Try simple HTML to PDF using browser print
        try:
            # Preprocess content to fix line break issues
            processed_content = self._fix_line_breaks(content)

            # Convert markdown to HTML with better line break handling
            try:
                html_content = markdown.markdown(
                    processed_content,
                    extensions=["extra", "nl2br"],  # nl2br: newlines to <br>
                )
            except Exception:
                # Fallback if nl2br extension is not available
                html_content = markdown.markdown(
                    processed_content, extensions=["extra"]
                )
                # Manually convert remaining single line breaks to <br>
                html_content = html_content.replace("\n", "<br>\n")

            # Add boxes around program summaries after markdown conversion
            print(
                f"[SERVER] HTML content before boxing (first 500 chars): "
                f"{html_content[:500]}"
            )
            html_content = self._add_program_boxes_html(html_content)
            print(
                f"[SERVER] HTML content after boxing (first 500 chars): "
                f"{html_content[:500]}"
            )

            # Get the logo as base64
            logo_data_uri = self._get_logo_base64()

            # Create a well-formatted HTML document
            html_full = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Meta Generation {generation}</title>
    <style>
        @media print {{
            @page {{ margin: 2cm; size: A4; }}
            body {{ font-size: 12pt; }}
        }}
        body {{ 
            font-family: 'Times New Roman', Times, serif; 
            line-height: 1.6; 
            color: #333;
            max-width: 800px;
            margin: 0 auto;
            padding: 20px;
        }}
        h1 {{ 
            color: #2c3e50; 
            border-bottom: 2px solid #e74c3c;
            padding-bottom: 10px;
            margin-top: 0;
        }}
        h2, h3 {{ 
            color: #2c3e50; 
            margin-top: 1.5em;
            margin-bottom: 0.5em;
        }}
        pre {{ 
            background-color: #f8f9fa; 
            padding: 15px; 
            border-radius: 5px; 
            overflow-x: auto;
            border: 1px solid #e9ecef;
            font-family: 'Courier New', monospace;
            font-size: 11pt;
        }}
        code {{ 
            background-color: #f8f9fa; 
            padding: 2px 4px; 
            border-radius: 3px;
            font-family: 'Courier New', monospace;
            font-size: 90%;
        }}
        blockquote {{ 
            border-left: 4px solid #e74c3c; 
            margin: 1em 0; 
            padding-left: 1em;
            color: #6c757d;
            font-style: italic;
        }}
        p {{ 
            margin-bottom: 1em; 
            line-height: 1.6;
            text-align: justify;
        }}
        ul, ol {{ margin-bottom: 1em; }}
        li {{ 
            margin-bottom: 0.5em; 
            line-height: 1.5;
        }}
        br {{ 
            line-height: 1.8; 
        }}
        /* Improve spacing for specific content types */
        strong {{ 
            font-weight: bold; 
            color: #2c3e50;
        }}
        em {{ 
            font-style: italic; 
            color: #34495e;
        }}
        /* Header with centered logo styling */
        .header-container {{
            text-align: center;
            margin-bottom: 2em;
            padding-bottom: 1em;
            border-bottom: 2px solid #e74c3c;
        }}
        .header-logo {{
            width: 150px;
            height: 150px;
            margin: 0 auto 15px auto;
            display: block;
        }}
        .header-title {{
            margin: 0;
            color: #2c3e50;
            font-size: 24pt;
            font-weight: bold;
            text-align: center;
        }}
        /* Program summary boxes */
        .program-box {{
            border: 2px solid #e74c3c;
            border-radius: 10px;
            margin: 0.8em 0;
            padding: 0.1em 0.8em;
            background-color: #f8f9fa;
            page-break-inside: avoid;
        }}
        .program-name {{
            font-weight: bold;
            color: #2c3e50;
            font-size: 16pt;
            margin-bottom: 1em;
            border-bottom: 1px solid #bdc3c7;
            padding-bottom: 0.5em;
        }}
        .program-field {{
            margin-top: 1em;
            margin-bottom: 0.5em;
        }}
        .program-field strong {{
            color: #34495e;
            font-weight: bold;
        }}
    </style>
</head>
<body>
    <div class="header-container">
        {f'<img src="{logo_data_uri}" alt="Shinka Logo" class="header-logo">' if logo_data_uri else ""}
        <h1 class="header-title">ShinkaEvolve Meta-Scratchpad: \
{generation}</h1>
    </div>
    {html_content}
</body>
</html>"""

            # Try wkhtmltopdf if available
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w", suffix=".html", delete=False
                ) as html_file:
                    html_file.write(html_full)
                    html_file_path = html_file.name

                with tempfile.NamedTemporaryFile(
                    suffix=".pdf", delete=False
                ) as pdf_file:
                    pdf_file_path = pdf_file.name

                # Try wkhtmltopdf directly
                result = subprocess.run(
                    [
                        "wkhtmltopdf",
                        "--page-size",
                        "A4",
                        "--margin-top",
                        "20mm",
                        "--margin-bottom",
                        "20mm",
                        "--margin-left",
                        "20mm",
                        "--margin-right",
                        "20mm",
                        html_file_path,
                        pdf_file_path,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )

                if result.returncode == 0:
                    with open(pdf_file_path, "rb") as f:
                        pdf_bytes = f.read()
                    print("[SERVER] PDF generated successfully using wkhtmltopdf")
                    return pdf_bytes
                else:
                    print(f"[SERVER] wkhtmltopdf failed: {result.stderr}")

            except (subprocess.TimeoutExpired, FileNotFoundError) as e:
                print(f"[SERVER] wkhtmltopdf not available: {e}")
            finally:
                # Clean up temp files
                try:
                    os.unlink(html_file_path)
                    os.unlink(pdf_file_path)
                except (NameError, OSError):
                    pass

            # Try pandoc as fallback
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w", suffix=".html", delete=False
                ) as html_file:
                    html_file.write(html_full)
                    html_file_path = html_file.name

                with tempfile.NamedTemporaryFile(
                    suffix=".pdf", delete=False
                ) as pdf_file:
                    pdf_file_path = pdf_file.name

                result = subprocess.run(
                    ["pandoc", html_file_path, "-o", pdf_file_path],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )

                if result.returncode == 0:
                    with open(pdf_file_path, "rb") as f:
                        pdf_bytes = f.read()
                    print("[SERVER] PDF generated successfully using pandoc")
                    return pdf_bytes
                else:
                    print(f"[SERVER] pandoc failed: {result.stderr}")

            except (subprocess.TimeoutExpired, FileNotFoundError) as e:
                print(f"[SERVER] pandoc not available: {e}")
            finally:
                # Clean up temp files
                try:
                    os.unlink(html_file_path)
                    os.unlink(pdf_file_path)
                except (NameError, OSError):
                    pass

        except Exception as e:
            print(f"[SERVER] HTML generation failed: {e}")

        print("[SERVER] All PDF generation methods failed")
        return None

    def _fix_line_breaks(self, content: str) -> str:
        """Fix line breaks in markdown content for better PDF rendering."""

        # Simple approach: ensure proper paragraph breaks
        # Replace single newlines that should be paragraph breaks with
        # double newlines

        # First, normalize line endings
        content = content.replace("\r\n", "\n").replace("\r", "\n")

        # Split into lines
        lines = content.split("\n")
        result_lines = []

        i = 0
        while i < len(lines):
            current_line = lines[i].strip()

            # Always add the current line
            result_lines.append(current_line)

            # Look ahead to see if we need to add extra spacing
            if i < len(lines) - 1:
                next_line = lines[i + 1].strip()

                # Add extra line break for paragraph separation if:
                # 1. Current line has substantial content
                # 2. Next line starts a new thought (capital letter)
                # 3. Neither line is a markdown special element
                if (
                    current_line
                    and next_line
                    and len(current_line) > 30  # Substantial content
                    and current_line.endswith((".", "!", "?", ";"))  # Sentence ending
                    and next_line[0].isupper()  # Next starts with capital
                    and not next_line.startswith(
                        ("#", "-", "*", "+")
                    )  # Not markdown list/header
                    and not re.match(r"^\*\*\w+:\*\*", next_line)
                ):  # Not bold field
                    result_lines.append("")  # Add blank line

            i += 1

        return "\n".join(result_lines)

    def _add_program_boxes_html(self, html_content: str) -> str:
        """Add HTML boxes around program summaries in converted HTML."""

        # Match entire <p> tags that contain program summaries
        # Pattern matches <p> tags that start with <strong>Program Name:
        program_pattern = r"(<p><strong>Program Name:[^<]*</strong>[\s\S]*?</p>)"

        def wrap_program_html(match):
            program_html = match.group(1).strip()
            return f'<div class="program-box">{program_html}</div>'

        # Replace all program summaries with boxed versions
        result = re.sub(
            program_pattern,
            wrap_program_html,
            html_content,
            flags=re.MULTILINE | re.DOTALL,
        )

        return result

    def _get_logo_base64(self) -> str:
        """Get the Shinka logo as base64 data URI."""
        try:
            # Look for favicon.png in the main shinka package directory
            logo_path = os.path.join(os.path.dirname(__file__), "favicon.png")
            if os.path.exists(logo_path):
                with open(logo_path, "rb") as f:
                    logo_data = f.read()
                encoded = base64.b64encode(logo_data).decode("utf-8")
                return f"data:image/png;base64,{encoded}"
        except Exception as e:
            print(f"[SERVER] Could not load logo: {e}")
        return ""

    # ========== New Run Feature Handlers ==========

    def _read_json_body(self) -> Dict[str, Any]:
        """Read and parse JSON request body."""
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)
        return json.loads(body.decode("utf-8"))

    def handle_credentials_check(self):
        """Check which LLM providers have valid credentials."""
        try:
            checker = CredentialChecker()
            result = checker.check_all()
            self.send_json_response({"ok": True, "credentials": result})
        except Exception as e:
            print(f"[SERVER] Error checking credentials: {e}")
            self.send_json_response({"ok": False, "error": str(e)})

    def handle_presets_list(self):
        """List all available presets."""
        try:
            manager = PresetManager()
            presets = manager.list_presets()
            self.send_json_response({"ok": True, "presets": presets})
        except Exception as e:
            print(f"[SERVER] Error listing presets: {e}")
            self.send_json_response({"ok": False, "error": str(e)})

    def handle_preset_get(self, preset_id: str):
        """Get a specific preset by ID."""
        try:
            manager = PresetManager()
            preset = manager.get_preset(preset_id)
            if preset:
                from dataclasses import asdict
                self.send_json_response({"ok": True, "preset": asdict(preset)})
            else:
                self.send_json_response({"ok": False, "error": "Preset not found"})
        except Exception as e:
            print(f"[SERVER] Error getting preset: {e}")
            self.send_json_response({"ok": False, "error": str(e)})

    def handle_preset_save(self):
        """Save a new preset."""
        try:
            data = self._read_json_body()
            manager = PresetManager()
            preset = manager.save_preset(
                name=data.get("name", "Unnamed Preset"),
                config=data.get("config", {}),
                description=data.get("description", ""),
                preset_id=data.get("id"),
            )
            from dataclasses import asdict
            self.send_json_response({"ok": True, "preset": asdict(preset)})
        except Exception as e:
            print(f"[SERVER] Error saving preset: {e}")
            self.send_json_response({"ok": False, "error": str(e)})

    def handle_preset_delete(self, preset_id: str):
        """Delete a preset."""
        try:
            manager = PresetManager()
            deleted = manager.delete_preset(preset_id)
            self.send_json_response({"ok": True, "deleted": deleted})
        except Exception as e:
            print(f"[SERVER] Error deleting preset: {e}")
            self.send_json_response({"ok": False, "error": str(e)})

    def handle_config_defaults(self):
        """Return default configuration values for the UI form."""
        try:
            from dataclasses import asdict
            defaults = asdict(UIRunConfig())
            self.send_json_response({"ok": True, "defaults": defaults})
        except Exception as e:
            print(f"[SERVER] Error getting config defaults: {e}")
            self.send_json_response({"ok": False, "error": str(e)})

    def handle_browse_folders(self, query: Dict[str, Any]):
        """Browse filesystem folders for the folder picker."""
        try:
            import os
            from pathlib import Path

            # Get the requested path, default to home directory
            requested_path = query.get("path", [""])[0]
            if not requested_path:
                requested_path = str(Path.home())

            current_path = Path(requested_path).resolve()

            # Security: don't allow browsing system directories
            blocked_prefixes = ["/System", "/Library", "/bin", "/sbin", "/usr", "/private/var"]
            if any(str(current_path).startswith(prefix) for prefix in blocked_prefixes):
                self.send_json_response({
                    "ok": False,
                    "error": "Access to system directories is not allowed"
                })
                return

            if not current_path.exists():
                self.send_json_response({
                    "ok": False,
                    "error": f"Path does not exist: {current_path}"
                })
                return

            if not current_path.is_dir():
                self.send_json_response({
                    "ok": False,
                    "error": f"Path is not a directory: {current_path}"
                })
                return

            # Get subdirectories (not files)
            folders = []
            try:
                for entry in sorted(current_path.iterdir()):
                    if entry.is_dir() and not entry.name.startswith('.'):
                        try:
                            # Check if we can access it
                            list(entry.iterdir())
                            folders.append({
                                "name": entry.name,
                                "path": str(entry),
                            })
                        except PermissionError:
                            # Skip inaccessible directories
                            pass
            except PermissionError:
                self.send_json_response({
                    "ok": False,
                    "error": f"Permission denied: {current_path}"
                })
                return

            # Compute parent path
            parent_path = str(current_path.parent) if current_path.parent != current_path else None

            # Quick access locations
            home = Path.home()
            quick_access = [
                {"name": "Home", "path": str(home)},
                {"name": "Desktop", "path": str(home / "Desktop")},
                {"name": "Documents", "path": str(home / "Documents")},
            ]
            # Add workspace if it exists
            workspace = home / "workspace"
            if workspace.exists():
                quick_access.append({"name": "Workspace", "path": str(workspace)})
            # Add /tmp
            quick_access.append({"name": "Temp (/tmp)", "path": "/tmp"})

            self.send_json_response({
                "ok": True,
                "current_path": str(current_path),
                "parent_path": parent_path,
                "folders": folders,
                "quick_access": quick_access,
            })
        except Exception as e:
            print(f"[SERVER] Error browsing folders: {e}")
            self.send_json_response({"ok": False, "error": str(e)})

    def handle_folder_picker_native(self, query: Dict[str, Any]):
        """Open native OS folder picker dialog using platform-specific methods."""
        import subprocess
        import platform
        from pathlib import Path

        initial_dir_list = query.get("initial_dir", [""])
        initial_dir = initial_dir_list[0] if initial_dir_list else ""
        if not initial_dir:
            initial_dir = str(Path.home())

        # Ensure initial_dir exists, otherwise use home
        if not Path(initial_dir).exists():
            initial_dir = str(Path.home())

        system = platform.system()

        try:
            if system == "Darwin":  # macOS
                # Use osascript (AppleScript) for native macOS folder dialog
                script = f'''
                    set defaultPath to POSIX file "{initial_dir}"
                    try
                        set selectedFolder to choose folder with prompt "Select Project Folder" default location defaultPath
                        return POSIX path of selectedFolder
                    on error
                        return ""
                    end try
                '''
                result = subprocess.run(
                    ["osascript", "-e", script],
                    capture_output=True,
                    text=True,
                    timeout=120
                )
                folder = result.stdout.strip()
                if folder:
                    # Remove trailing slash if present
                    folder = folder.rstrip("/")
                    self.send_json_response({"ok": True, "path": folder})
                else:
                    # User cancelled or error
                    self.send_json_response({"ok": True, "path": None})

            elif system == "Windows":
                # Use PowerShell for native Windows folder dialog
                ps_script = f'''
                Add-Type -AssemblyName System.Windows.Forms
                $dialog = New-Object System.Windows.Forms.FolderBrowserDialog
                $dialog.Description = "Select Project Folder"
                $dialog.SelectedPath = "{initial_dir}"
                $dialog.ShowNewFolderButton = $true
                if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {{
                    Write-Output $dialog.SelectedPath
                }}
                '''
                result = subprocess.run(
                    ["powershell", "-Command", ps_script],
                    capture_output=True,
                    text=True,
                    timeout=120
                )
                folder = result.stdout.strip()
                if folder:
                    self.send_json_response({"ok": True, "path": folder})
                else:
                    self.send_json_response({"ok": True, "path": None})

            elif system == "Linux":
                # Try zenity first (common on GNOME), then kdialog (KDE), then fallback
                folder = None

                # Try zenity
                try:
                    result = subprocess.run(
                        ["zenity", "--file-selection", "--directory",
                         "--title=Select Project Folder",
                         f"--filename={initial_dir}/"],
                        capture_output=True,
                        text=True,
                        timeout=120
                    )
                    if result.returncode == 0:
                        folder = result.stdout.strip()
                except FileNotFoundError:
                    pass

                # Try kdialog if zenity not available
                if folder is None:
                    try:
                        result = subprocess.run(
                            ["kdialog", "--getexistingdirectory", initial_dir,
                             "--title", "Select Project Folder"],
                            capture_output=True,
                            text=True,
                            timeout=120
                        )
                        if result.returncode == 0:
                            folder = result.stdout.strip()
                    except FileNotFoundError:
                        pass

                if folder:
                    self.send_json_response({"ok": True, "path": folder})
                elif folder == "":
                    # User cancelled
                    self.send_json_response({"ok": True, "path": None})
                else:
                    # No dialog tool available, fallback to custom browser
                    self.send_json_response({
                        "ok": False,
                        "error": "No native dialog available (install zenity or kdialog)",
                        "fallback": True
                    })
            else:
                # Unknown platform, fallback
                self.send_json_response({
                    "ok": False,
                    "error": f"Unsupported platform: {system}",
                    "fallback": True
                })

        except subprocess.TimeoutExpired:
            self.send_json_response({"ok": False, "error": "Dialog timed out", "fallback": True})
        except Exception as e:
            print(f"[SERVER] Error in native folder picker: {e}")
            self.send_json_response({"ok": False, "error": str(e), "fallback": True})

    def handle_git_prepare(self):
        """Prepare a git workspace (clone or worktree)."""
        try:
            data = self._read_json_body()
            git_url = data.get("git_url")
            branch = data.get("branch", "main")
            use_worktree = data.get("use_worktree", True)
            workspace_name = data.get("workspace_name")

            if not git_url:
                self.send_json_response({"ok": False, "error": "git_url is required"})
                return

            manager = GitWorktreeManager()
            info = manager.prepare_workspace(
                git_url=git_url,
                branch=branch,
                use_worktree=use_worktree,
                workspace_name=workspace_name,
            )

            self.send_json_response({
                "ok": True,
                "workspace_path": str(info.path),
                "commit_sha": info.commit_sha,
                "is_worktree": info.is_worktree,
            })
        except Exception as e:
            print(f"[SERVER] Error preparing git workspace: {e}")
            self.send_json_response({"ok": False, "error": str(e)})

    # ========== CLI Config Handlers ==========

    def handle_cli_config_get(self, provider: str):
        """Get CLI configuration for a provider."""
        try:
            manager = get_cli_config_manager(provider)
            if manager is None:
                # ShinkaAgent doesn't have external CLI config
                self.send_json_response({
                    "ok": True,
                    "provider": provider,
                    "system_prompt": None,
                    "mcp_servers": [],
                    "allowed_tools": None,
                    "extra_config": {},
                    "supports_mcp": False,
                    "supports_allowed_tools": False,
                })
                return

            config = manager.load_config()
            self.send_json_response({
                "ok": True,
                "provider": provider,
                "system_prompt": config.system_prompt,
                "mcp_servers": [s.to_dict() for s in config.mcp_servers],
                "allowed_tools": config.allowed_tools,
                "extra_config": config.extra_config,
                "supports_mcp": provider in ("gemini", "claude"),
                "supports_allowed_tools": provider == "claude",
            })
        except Exception as e:
            print(f"[SERVER] Error loading CLI config for {provider}: {e}")
            self.send_json_response({"ok": False, "error": str(e)})

    def handle_cli_config_save(self, provider: str):
        """Save CLI configuration for a provider."""
        try:
            data = self._read_json_body()
            manager = get_cli_config_manager(provider)

            if manager is None:
                self.send_json_response({
                    "ok": False,
                    "error": f"Provider {provider} does not support CLI configuration"
                })
                return

            # Parse MCP servers from request
            mcp_servers = []
            for s in data.get("mcp_servers", []):
                mcp_servers.append(MCPServer(
                    name=s.get("name", ""),
                    command=s.get("command", ""),
                    args=s.get("args", []),
                    env=s.get("env"),
                ))

            config = CLIConfig(
                system_prompt=data.get("system_prompt"),
                mcp_servers=mcp_servers,
                allowed_tools=data.get("allowed_tools"),
                extra_config=data.get("extra_config", {}),
            )

            manager.save_config(config)
            self.send_json_response({"ok": True})
        except Exception as e:
            print(f"[SERVER] Error saving CLI config for {provider}: {e}")
            self.send_json_response({"ok": False, "error": str(e)})

    def handle_cli_config_mcp_list(self, provider: str):
        """List MCP servers for a provider."""
        try:
            manager = get_cli_config_manager(provider)
            if manager is None or provider not in ("gemini", "claude"):
                self.send_json_response({
                    "ok": True,
                    "mcp_servers": [],
                    "supports_mcp": False,
                })
                return

            config = manager.load_config()
            self.send_json_response({
                "ok": True,
                "mcp_servers": [s.to_dict() for s in config.mcp_servers],
                "supports_mcp": True,
            })
        except Exception as e:
            print(f"[SERVER] Error listing MCP servers for {provider}: {e}")
            self.send_json_response({"ok": False, "error": str(e)})

    def handle_cli_config_mcp_add(self, provider: str):
        """Add an MCP server for a provider."""
        try:
            data = self._read_json_body()
            manager = get_cli_config_manager(provider)

            if manager is None or provider not in ("gemini", "claude"):
                self.send_json_response({
                    "ok": False,
                    "error": f"Provider {provider} does not support MCP servers"
                })
                return

            server = MCPServer(
                name=data.get("name", ""),
                command=data.get("command", ""),
                args=data.get("args", []),
                env=data.get("env"),
            )

            if not server.name or not server.command:
                self.send_json_response({
                    "ok": False,
                    "error": "MCP server name and command are required"
                })
                return

            manager.add_mcp_server(server)
            self.send_json_response({"ok": True})
        except Exception as e:
            print(f"[SERVER] Error adding MCP server for {provider}: {e}")
            self.send_json_response({"ok": False, "error": str(e)})

    def handle_cli_config_mcp_delete(self, provider: str, mcp_name: str):
        """Delete an MCP server for a provider."""
        try:
            manager = get_cli_config_manager(provider)

            if manager is None or provider not in ("gemini", "claude"):
                self.send_json_response({
                    "ok": False,
                    "error": f"Provider {provider} does not support MCP servers"
                })
                return

            # URL decode the MCP name
            mcp_name = urllib.parse.unquote(mcp_name)
            removed = manager.remove_mcp_server(mcp_name)

            if removed:
                self.send_json_response({"ok": True})
            else:
                self.send_json_response({
                    "ok": False,
                    "error": f"MCP server '{mcp_name}' not found"
                })
        except Exception as e:
            print(f"[SERVER] Error deleting MCP server for {provider}: {e}")
            self.send_json_response({"ok": False, "error": str(e)})

    def handle_cli_config_profiles_list(self, provider: str):
        """List available profiles for a provider."""
        try:
            # Get the selected profile manager
            selected_mgr = get_selected_profiles_manager()
            selected = selected_mgr.get_selected(provider)

            if provider == "codex":
                manager = CodexProfileManager()
                profiles = manager.list_profiles()
                self.send_json_response({
                    "ok": True,
                    "provider": provider,
                    "profiles": profiles,
                    "selected": selected.get("profile", "default"),
                    "sandbox": selected.get("sandbox", "workspace-write"),
                })
            elif provider == "gemini":
                manager = GeminiConfigManager()
                system_prompts = manager.list_system_prompts()
                self.send_json_response({
                    "ok": True,
                    "provider": provider,
                    "system_prompts": system_prompts,
                    "selected_system_prompt": selected.get("system_prompt_file"),
                    "sandbox_disabled": selected.get("sandbox_disabled", False),
                })
            elif provider == "claude":
                # Claude doesn't have native profiles yet
                self.send_json_response({
                    "ok": True,
                    "provider": provider,
                    "profiles": [],
                    "skip_permissions": selected.get("skip_permissions", True),
                })
            else:
                self.send_json_response({
                    "ok": False,
                    "error": f"Unknown provider: {provider}"
                })
        except Exception as e:
            print(f"[SERVER] Error listing profiles for {provider}: {e}")
            self.send_json_response({"ok": False, "error": str(e)})

    def handle_cli_config_profiles_update(self, provider: str):
        """Update selected profile for a provider."""
        try:
            data = self._read_json_body()
            selected_mgr = get_selected_profiles_manager()

            if provider == "codex":
                selection = {
                    "profile": data.get("profile", "default"),
                    "sandbox": data.get("sandbox", "workspace-write"),
                }
            elif provider == "gemini":
                selection = {
                    "system_prompt_file": data.get("system_prompt_file"),
                    "sandbox_disabled": data.get("sandbox_disabled", False),
                }
                # If creating a new system prompt
                if data.get("create_system_prompt"):
                    name = data.get("new_prompt_name", "custom")
                    content = data.get("new_prompt_content", "")
                    manager = GeminiConfigManager()
                    path = manager.create_system_prompt(name, content)
                    selection["system_prompt_file"] = path
            elif provider == "claude":
                selection = {
                    "skip_permissions": data.get("skip_permissions", True),
                }
            else:
                self.send_json_response({
                    "ok": False,
                    "error": f"Unknown provider: {provider}"
                })
                return

            selected_mgr.set_selected(provider, selection)
            self.send_json_response({"ok": True, "selection": selection})
        except Exception as e:
            print(f"[SERVER] Error updating profile for {provider}: {e}")
            self.send_json_response({"ok": False, "error": str(e)})

    # ========== End CLI Config Handlers ==========

    # ========== Workspace Diff & Patch Handlers ==========

    def handle_workspace_diff(self, query: Dict[str, Any]):
        """Get diff from original for a program in an isolated workspace.

        Query params:
            db_path: Path to the evolution database
            program_id: (optional) ID of the program to diff. If not provided,
                       returns diff for all uncommitted changes.
            from_original: (optional) If "true", diff from initial commit.
                          Otherwise diff from parent commit.
        """
        import subprocess

        try:
            db_path = query.get("db_path", [""])[0]
            program_id = query.get("program_id", [""])[0]
            from_original = query.get("from_original", ["true"])[0].lower() == "true"

            # Resolve the database path
            abs_db_path = self._resolve_db_path(db_path)
            if not abs_db_path or not Path(abs_db_path).exists():
                self.send_json_response({
                    "ok": False,
                    "error": f"Database not found: {db_path}"
                })
                return

            # Workspace is in the same directory as the database
            results_dir = Path(abs_db_path).parent
            workspace_path = results_dir / "workspace"

            if not workspace_path.exists():
                self.send_json_response({
                    "ok": False,
                    "error": "No isolated workspace found for this run"
                })
                return

            git_dir = workspace_path / ".git"
            if not git_dir.exists():
                self.send_json_response({
                    "ok": False,
                    "error": "Workspace is not a git repository"
                })
                return

            # Get the commit SHA for the program if specified
            target_commit = "HEAD"
            if program_id:
                # Look up the program's commit_sha from the database
                con = sqlite3.connect(abs_db_path)
                cur = con.cursor()
                cur.execute("SELECT metadata FROM programs WHERE id = ?", (program_id,))
                row = cur.fetchone()
                con.close()

                if row and row[0]:
                    import json
                    metadata = json.loads(row[0])
                    if "commit_sha" in metadata:
                        target_commit = metadata["commit_sha"]

            if from_original:
                # Get the first commit (initial state)
                result = subprocess.run(
                    ["git", "rev-list", "--max-parents=0", "HEAD"],
                    cwd=workspace_path,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if result.returncode != 0:
                    self.send_json_response({
                        "ok": False,
                        "error": "Failed to find initial commit"
                    })
                    return
                first_commit = result.stdout.strip().split('\n')[0]

                # Get diff from first commit to target
                result = subprocess.run(
                    ["git", "diff", first_commit, target_commit],
                    cwd=workspace_path,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
            else:
                # Diff from parent commit
                result = subprocess.run(
                    ["git", "diff", f"{target_commit}^", target_commit],
                    cwd=workspace_path,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )

            diff_text = result.stdout
            self.send_json_response({
                "ok": True,
                "diff": diff_text,
                "workspace_path": str(workspace_path),
                "target_commit": target_commit,
                "from_original": from_original,
            })

        except subprocess.SubprocessError as e:
            self.send_json_response({"ok": False, "error": f"Git error: {e}"})
        except Exception as e:
            print(f"[SERVER] Error getting workspace diff: {e}")
            import traceback
            traceback.print_exc()
            self.send_json_response({"ok": False, "error": str(e)})

    def handle_export_patch(self, query: Dict[str, Any]):
        """Export a patch file for a program's changes.

        Query params:
            db_path: Path to the evolution database
            program_id: ID of the program to export patch for
        """
        import subprocess
        import tempfile

        try:
            db_path = query.get("db_path", [""])[0]
            program_id = query.get("program_id", [""])[0]

            if not program_id:
                self.send_json_response({
                    "ok": False,
                    "error": "program_id is required"
                })
                return

            # Resolve the database path
            abs_db_path = self._resolve_db_path(db_path)
            if not abs_db_path or not Path(abs_db_path).exists():
                self.send_json_response({
                    "ok": False,
                    "error": f"Database not found: {db_path}"
                })
                return

            # Workspace is in the same directory as the database
            results_dir = Path(abs_db_path).parent
            workspace_path = results_dir / "workspace"

            if not workspace_path.exists():
                self.send_json_response({
                    "ok": False,
                    "error": "No isolated workspace found for this run"
                })
                return

            # Look up the program's commit_sha from the database
            con = sqlite3.connect(abs_db_path)
            cur = con.cursor()
            cur.execute("SELECT metadata, generation FROM programs WHERE id = ?", (program_id,))
            row = cur.fetchone()
            con.close()

            if not row:
                self.send_json_response({
                    "ok": False,
                    "error": f"Program not found: {program_id}"
                })
                return

            metadata_str, generation = row
            commit_sha = None
            if metadata_str:
                import json
                metadata = json.loads(metadata_str)
                commit_sha = metadata.get("commit_sha")

            if not commit_sha:
                self.send_json_response({
                    "ok": False,
                    "error": "Program does not have a commit SHA (may not be from isolated workspace)"
                })
                return

            # Get the first commit for diffing
            result = subprocess.run(
                ["git", "rev-list", "--max-parents=0", "HEAD"],
                cwd=workspace_path,
                capture_output=True,
                text=True,
                timeout=30,
            )
            first_commit = result.stdout.strip().split('\n')[0]

            # Generate the diff as a patch
            result = subprocess.run(
                ["git", "diff", first_commit, commit_sha],
                cwd=workspace_path,
                capture_output=True,
                text=True,
                timeout=60,
            )

            patch_content = result.stdout

            # Create a header for the patch
            patch_header = f"""# Shinka Evolution Patch
# Program ID: {program_id}
# Generation: {generation}
# Commit: {commit_sha}
# Generated from isolated workspace
#
# To apply this patch:
#   git apply evolution_patch.patch
#
# Or for a three-way merge:
#   git apply --3way evolution_patch.patch
#
"""
            full_patch = patch_header + patch_content

            # Send as downloadable file
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Disposition", f'attachment; filename="evolution_gen{generation}_{program_id[:8]}.patch"')
            self.send_header("Content-Length", str(len(full_patch.encode('utf-8'))))
            self.end_headers()
            self.wfile.write(full_patch.encode('utf-8'))

        except subprocess.SubprocessError as e:
            self.send_json_response({"ok": False, "error": f"Git error: {e}"})
        except Exception as e:
            print(f"[SERVER] Error exporting patch: {e}")
            import traceback
            traceback.print_exc()
            self.send_json_response({"ok": False, "error": str(e)})

    # ========== End Workspace Diff & Patch Handlers ==========

    def handle_evolution_run_validate(self):
        """Validate run configuration without starting."""
        try:
            data = self._read_json_body()
            flat = flatten_nested_config(data)
            ui_config = UIRunConfig(**flat)
            
            # ... (rest of validation logic stays same, using flattened config for consistent validation)
            # Basic field validation
            errors = []
            warnings = []

            # Validate local path exists
            if ui_config.source_type == "local":
                path = Path(ui_config.local_path)
                if not path.exists():
                    errors.append({
                        "field": "codebase.local_path",
                        "message": f"Path does not exist: {ui_config.local_path}",
                    })
                elif not path.is_dir():
                    errors.append({
                        "field": "codebase.local_path",
                        "message": f"Path is not a directory: {ui_config.local_path}",
                    })
                # Optional: validate init_program_path if provided
                if ui_config.init_program_path:
                    init_path = path / ui_config.init_program_path if not Path(ui_config.init_program_path).is_absolute() else Path(ui_config.init_program_path)
                    # Only enforce existence if NOT in agentic mode
                    if not init_path.exists() and not ui_config.agentic_mode:
                        errors.append({
                            "field": "agent.init_program_path",
                            "message": f"Initial program path does not exist: {init_path}",
                        })

            # Validate git URL
            if ui_config.source_type == "git":
                if not ui_config.git_url:
                    errors.append({
                        "field": "codebase.git_url",
                        "message": "Git URL is required",
                    })

            # Check credentials for requested models
            checker = CredentialChecker()
            validation = checker.validate_models(ui_config.llm_models)
            if not validation["valid"]:
                for provider in validation["missing_providers"]:
                    errors.append({
                        "field": "agent.llm_models",
                        "message": f"{provider} credentials not configured",
                    })

            # Warnings
            if ui_config.agent_max_turns > 100:
                warnings.append({
                    "field": "agent.max_turns",
                    "message": "High turn count may significantly increase costs",
                })

            if ui_config.num_generations > 100:
                warnings.append({
                    "field": "run.num_generations",
                    "message": "Large number of generations will take a long time",
                })

            self.send_json_response({
                "ok": True,
                "valid": len(errors) == 0,
                "errors": errors,
                "warnings": warnings,
                "credentials": checker.check_all(),
            })
        except Exception as e:
            print(f"[SERVER] Error validating config: {e}")
            import traceback
            traceback.print_exc()
            self.send_json_response({"ok": False, "error": str(e)})

    def handle_evolution_run_export(self):
        """Export run configuration as a CLI command."""
        try:
            data = self._read_json_body()
            flat = flatten_nested_config(data)
            ui_config = UIRunConfig(**flat)

            # Start building command
            cmd_parts = ["uv", "run", "shinka_launch"]

            # Map Agent settings
            if ui_config.agentic_mode:
                cmd_parts.append("evo_config.agentic_mode=true")
                cmd_parts.append(f"evo_config.agentic.backend={ui_config.agent_backend}")
                cmd_parts.append(f"evo_config.agentic.sandbox={ui_config.agent_sandbox}")
                cmd_parts.append(f"evo_config.agentic.max_turns={ui_config.agent_max_turns}")
                if ui_config.agent_max_seconds > 0:
                    cmd_parts.append(f"evo_config.agentic.max_seconds={ui_config.agent_max_seconds}")
                if ui_config.llm_models:
                    # Hydra list syntax: "[a,b]"
                    models_str = ",".join(ui_config.llm_models)
                    cmd_parts.append(f"evo_config.llm_models='[{models_str}]'")
            else:
                 cmd_parts.append("evo_config.agentic_mode=false")

            # Task Prompt (Agent Instruction)
            if ui_config.task_sys_msg:
                # Escape single quotes for shell (' -> '\'')
                escaped = ui_config.task_sys_msg.replace("'", "'\\''")
                cmd_parts.append(f"evo_config.task_sys_msg='{escaped}'")

            # Evaluator settings
            cmd_parts.append(f"evo_config.evaluator.mode={ui_config.evaluator_mode}")
            if ui_config.evaluator_mode == "agentic":
                # Only if using agentic evaluator
                cmd_parts.append(f"evo_config.evaluator.agentic.backend={ui_config.eval_backend}")
                cmd_parts.append(f"evo_config.evaluator.agentic.max_turns={ui_config.eval_max_turns}")
                if ui_config.eval_prompt:
                     escaped_eval = ui_config.eval_prompt.replace("'", "'\\''")
                     cmd_parts.append(f"evo_config.evaluator.agentic.eval_prompt='{escaped_eval}'")
            
            # Run settings
            cmd_parts.append(f"evo_config.num_generations={ui_config.num_generations}")
            cmd_parts.append(f"evo_config.max_parallel_jobs={ui_config.max_parallel_jobs}")
            
            # Database settings
            cmd_parts.append(f"db_config.num_islands={ui_config.num_islands}")
            cmd_parts.append(f"db_config.archive_size={ui_config.archive_size}")
            cmd_parts.append(f"db_config.migration_interval={ui_config.migration_interval}")
            cmd_parts.append(f"db_config.migration_rate={ui_config.migration_rate}")
            cmd_parts.append(f"db_config.island_elitism={str(ui_config.island_elitism).lower()}")

            # Path settings
            # Note: For local path, users typically run from the root, so we use the relative path 
            # if it's inside, or absolute if outside. The UI passes absolute.
            if ui_config.init_program_path:
                cmd_parts.append(f"evo_config.init_program_path={ui_config.init_program_path}")
            else:
                cmd_parts.append("evo_config.init_program_path=null")
            if ui_config.init_support_dir:
                 cmd_parts.append(f"evo_config.init_support_dir={ui_config.init_support_dir}")

            # Job settings
            cmd_parts.append(f"evo_config.job_type={ui_config.job_type}")
            
            # Large codebase / embedding scale settings
            cmd_parts.append(f"evo_config.embedding_max_files={ui_config.embedding_max_files}")
            cmd_parts.append(f"evo_config.embedding_max_total_bytes={ui_config.embedding_max_total_bytes}")
            cmd_parts.append(f"evo_config.embedding_max_bytes_per_file={ui_config.embedding_max_bytes_per_file}")
            if ui_config.cleanup_old_generations:
                cmd_parts.append(f"evo_config.cleanup_old_generations=true")
                cmd_parts.append(f"evo_config.cleanup_keep_last_n={ui_config.cleanup_keep_last_n}")
            
            # Run Name
            if ui_config.run_name:
                cmd_parts.append(f"run_name={ui_config.run_name}")
            # Eval program path (allow empty to mean agent-only eval)
            if ui_config.eval_program_path:
                cmd_parts.append(f"job_config.eval_program_path={ui_config.eval_program_path}")
            else:
                cmd_parts.append("job_config.eval_program_path=null")

            command = " ".join(cmd_parts)

            self.send_json_response({
                "ok": True,
                "command": command,
                "message": "Command generated successfully"
            })
        except Exception as e:
            print(f"[SERVER] Error exporting config: {e}")
            import traceback
            traceback.print_exc()
            self.send_json_response({"ok": False, "error": str(e)})

    def handle_evolution_run_start(self):
        """Start a new evolution run."""
        try:
            data = self._read_json_body()
            flat = flatten_nested_config(data)
            ui_config = UIRunConfig(**flat)

            # Handle workspace setup based on source type and isolation setting
            workspace_path = None
            original_source_path = None  # Track original for reference
            pre_computed_results_dir = None  # Set when using isolated workspace
            manager = None  # Only create when needed for git operations

            # For isolated local workspaces, determine final run name BEFORE creating workspace
            # This handles auto-increment of run names if a previous run exists
            final_run_name = None
            if ui_config.source_type == "local" and ui_config.use_worktree:
                original_source_path = Path(ui_config.local_path).resolve()
                base_run_name = ui_config.run_name or "evolution_run"
                base_run_name = ''.join(c if c.isalnum() or c in '-_' else '_' for c in base_run_name)

                # Check for existing runs and auto-increment if needed
                run_name = base_run_name
                suffix = 2
                while True:
                    candidate_results_dir = original_source_path / "results" / f"shinka_{original_source_path.name}" / run_name
                    candidate_db = candidate_results_dir / "evolution_db.sqlite"

                    if not candidate_db.exists():
                        break  # No existing DB, use this name

                    # Check if existing DB has programs
                    try:
                        con = sqlite3.connect(candidate_db)
                        cur = con.cursor()
                        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='programs'")
                        has_table = cur.fetchone() is not None
                        row_count = 0
                        if has_table:
                            cur.execute("SELECT COUNT(*) FROM programs")
                            row_count = cur.fetchone()[0]
                        con.close()

                        if row_count == 0:
                            break  # Empty DB, safe to reuse

                        # Has programs - try next suffix
                        print(f"[SERVER] Run '{run_name}' exists with {row_count} programs, trying {base_run_name}-{suffix}")
                        run_name = f"{base_run_name}-{suffix}"
                        suffix += 1
                    except sqlite3.DatabaseError:
                        # Corrupted DB - try next suffix
                        print(f"[SERVER] Run '{run_name}' has corrupted DB, trying {base_run_name}-{suffix}")
                        run_name = f"{base_run_name}-{suffix}"
                        suffix += 1

                final_run_name = run_name
                print(f"[SERVER] Final run name for isolated workspace: {final_run_name}")

            if ui_config.source_type == "git":
                # Git source - clone to specified workspace path if provided, otherwise use temp dir
                manager = GitWorktreeManager()  # Create manager for git operations

                if ui_config.git_workspace_path:
                    # User specified a workspace path for the git clone
                    local_results_root = Path(ui_config.git_workspace_path).resolve()
                    base_run_name = ui_config.run_name or "evolution_run"
                    base_run_name = ''.join(c if c.isalnum() or c in '-_' else '_' for c in base_run_name)

                    # Auto-increment run name if needed
                    run_name = base_run_name
                    suffix = 2
                    while True:
                        candidate_results_dir = local_results_root / "results" / f"shinka_git" / run_name
                        candidate_db = candidate_results_dir / "evolution_db.sqlite"
                        if not candidate_db.exists():
                            break
                        try:
                            con = sqlite3.connect(candidate_db)
                            cur = con.cursor()
                            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='programs'")
                            has_table = cur.fetchone() is not None
                            row_count = 0
                            if has_table:
                                cur.execute("SELECT COUNT(*) FROM programs")
                                row_count = cur.fetchone()[0]
                            con.close()
                            if row_count == 0:
                                break
                            run_name = f"{base_run_name}-{suffix}"
                            suffix += 1
                        except sqlite3.DatabaseError:
                            run_name = f"{base_run_name}-{suffix}"
                            suffix += 1

                    final_run_name = run_name
                    pre_computed_results_dir = local_results_root / "results" / f"shinka_git" / final_run_name
                    git_workspace_path = pre_computed_results_dir / "workspace"

                    print(f"[SERVER] Cloning git repo to local path: {git_workspace_path}")

                    # Clone/worktree to the specified location
                    info = manager.prepare_workspace(
                        git_url=ui_config.git_url,
                        branch=ui_config.git_branch,
                        use_worktree=ui_config.use_worktree,
                        workspace_name=str(git_workspace_path),
                    )
                    workspace_path = info.path
                    original_source_path = ui_config.git_url
                else:
                    # No local path - use temp directory (legacy behavior)
                    print(f"[SERVER] No local path specified, cloning git repo to temp directory")
                    info = manager.prepare_workspace(
                        git_url=ui_config.git_url,
                        branch=ui_config.git_branch,
                        use_worktree=ui_config.use_worktree,
                    )
                    workspace_path = info.path
                    original_source_path = ui_config.git_url
                    pre_computed_results_dir = None
            else:
                # Local path source
                if original_source_path is None:
                    original_source_path = Path(ui_config.local_path).resolve()

                if ui_config.use_worktree:
                    # Create isolated workspace copy using the final run name
                    manager = GitWorktreeManager()  # Create manager for git operations
                    pre_computed_results_dir = original_source_path / "results" / f"shinka_{original_source_path.name}" / final_run_name
                    isolated_workspace_path = pre_computed_results_dir / "workspace"

                    print(f"[SERVER] Creating isolated workspace from {original_source_path}")
                    print(f"[SERVER] Isolated workspace location: {isolated_workspace_path}")

                    info = manager.create_local_copy(
                        source_path=original_source_path,
                        target_path=isolated_workspace_path,
                    )
                    workspace_path = info.path
                    print(f"[SERVER] Isolated workspace created with initial commit: {info.commit_sha[:8]}")
                else:
                    # No isolation - use original path directly (no git needed)
                    workspace_path = original_source_path
                    print(f"[SERVER] Using original path directly (no isolation): {workspace_path}")

            # Build configs
            builder = RunConfigBuilder(ui_config, workspace_path)
            evo_config = builder.build_evolution_config()
            db_config = builder.build_database_config()
            job_config = builder.build_job_config()
            results_dir = builder.get_results_dir()

            # If agentic and the user did not supply a real seed/eval script, allow empty
            if (
                evo_config.agentic_mode
                and evo_config.init_program_path
                and not Path(evo_config.init_program_path).exists()
            ):
                print(f"[SERVER] init_program_path not found, clearing for agentic run: {evo_config.init_program_path}")
                evo_config.init_program_path = ""
            if (
                evo_config.evaluator.mode == "agentic"
                and job_config.eval_program_path
                # Check in TARGET workspace, not server dir - eval script must exist there
                and not (workspace_path / job_config.eval_program_path).exists()
            ):
                print(f"[SERVER] eval_program_path not found in workspace, clearing for agentic eval: {job_config.eval_program_path}")
                job_config.eval_program_path = ""
                ui_config.eval_program_path = ""  # Also clear ui_config so overrides use null

            # Update results_dir in evo_config
            evo_config.results_dir = str(results_dir)

            # Build the CLI command to launch the evolution
            # This runs it as a subprocess so signal handlers work
            import subprocess
            import os

            # Hydra CLI expects: base_configs then ++overrides
            cmd_parts = [
                "uv", "run", "shinka_launch",
                # Base config - use agentic evolution with ui_custom task (nulls out Hydra defaults)
                "evolution=agentic",
                "task@_global_=ui_custom",
            ]

            # Determine run name and target results directory
            # For isolated local workspaces, this was already computed before creating workspace
            if final_run_name and pre_computed_results_dir:
                run_name = final_run_name
                target_results_dir = pre_computed_results_dir
            else:
                # Non-isolated or git source - compute now with auto-increment
                base_run_name = ui_config.run_name or results_dir.name
                base_run_name = ''.join(c if c.isalnum() or c in '-_' else '_' for c in base_run_name)

                run_name = base_run_name
                suffix = 2
                while True:
                    target_results_dir = workspace_path / "results" / f"shinka_{workspace_path.name}" / run_name
                    existing_db = target_results_dir / db_config.db_path

                    if not existing_db.exists():
                        break  # No existing DB, use this name

                    # Check if existing DB has programs
                    try:
                        con = sqlite3.connect(existing_db)
                        cur = con.cursor()
                        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='programs'")
                        has_table = cur.fetchone() is not None
                        row_count = 0
                        if has_table:
                            cur.execute("SELECT COUNT(*) FROM programs")
                            row_count = cur.fetchone()[0]
                        con.close()

                        if row_count == 0:
                            break  # Empty DB, safe to reuse

                        # Has programs - try next suffix
                        print(f"[SERVER] Run '{run_name}' exists with {row_count} programs, trying {base_run_name}-{suffix}")
                        run_name = f"{base_run_name}-{suffix}"
                        suffix += 1
                    except sqlite3.DatabaseError:
                        # Corrupted DB - try next suffix
                        print(f"[SERVER] Run '{run_name}' has corrupted DB, trying {base_run_name}-{suffix}")
                        run_name = f"{base_run_name}-{suffix}"
                        suffix += 1

            # Add Hydra overrides with ++ prefix
            overrides = [
                f"++evo_config.agentic_mode={str(evo_config.agentic_mode).lower()}",
                f"++evo_config.agentic.backend={evo_config.agentic.backend}",
                f"++evo_config.agentic.sandbox={evo_config.agentic.sandbox}",
                f"++evo_config.agentic.max_turns={evo_config.agentic.max_turns}",
                f"++evo_config.evaluator.mode={evo_config.evaluator.mode}",
                f"++evo_config.num_generations={evo_config.num_generations}",
                f"++evo_config.max_parallel_jobs={evo_config.max_parallel_jobs}",
                f"++db_config.num_islands={db_config.num_islands}",
                f"++db_config.archive_size={db_config.archive_size}",
                f"++evo_config.job_type={evo_config.job_type}",
                f"++run_name={run_name}",
                # Point to the target workspace seed if provided; null disables defaults
                f"++evo_config.init_program_path={evo_config.init_program_path or 'null'}",
                # Set results directory to be in the workspace
                f"++evo_config.results_dir={target_results_dir}",
                f"++output_dir={target_results_dir}",
            ]
            # Override eval prompt if provided
            if (
                evo_config.evaluator.mode == "agentic"
                and getattr(evo_config.evaluator.agentic, "eval_prompt", None) is not None
            ):
                escaped_eval = evo_config.evaluator.agentic.eval_prompt.replace("'", "\\'")
                overrides.append(f"++evo_config.evaluator.agentic.eval_prompt='{escaped_eval}'")

            # Override eval program path (allow empty to disable script-based eval)
            overrides.append(f"++job_config.eval_program_path={ui_config.eval_program_path or 'null'}")
            # If no eval script, also clear evaluate_function to avoid task defaults
            if not ui_config.eval_program_path:
                overrides.append("++evaluate_function=null")

            # Handle embedding model - disable if not enabled or no model specified
            embedding_model = evo_config.embedding_model
            if embedding_model:
                overrides.append(f"++evo_config.embedding_model={embedding_model}")
            else:
                overrides.append("++evo_config.embedding_model=null")

            # Add evaluator agentic config if mode is agentic
            if evo_config.evaluator.mode == "agentic" and evo_config.evaluator.agentic:
                overrides.append(f"++evo_config.evaluator.agentic.max_turns={evo_config.evaluator.agentic.max_turns or 80}")
                overrides.append(f"++evo_config.evaluator.agentic.sandbox={evo_config.evaluator.agentic.sandbox or 'workspace-write'}")
                if getattr(evo_config.evaluator.agentic, "eval_prompt", None) is not None:
                    escaped_eval = evo_config.evaluator.agentic.eval_prompt.replace("'", "\\'")
                    overrides.append(f"++evo_config.evaluator.agentic.eval_prompt='{escaped_eval}'")

            # Add task prompt if provided
            # Always override task_sys_msg to avoid inheriting defaults
            escaped_msg = (evo_config.task_sys_msg or "").replace("'", "\\'")
            overrides.append(f"++evo_config.task_sys_msg='{escaped_msg}'")

            # Add LLM models if specified
            if evo_config.llm_models:
                overrides.append(f"++evo_config.llm_models=[{','.join(evo_config.llm_models)}]")

            cmd_parts.extend(overrides)

            cmd_str = ' '.join(cmd_parts)

            # Run from the server's directory (where shinka is installed)
            # not from the target workspace, so uv finds the correct shinka package
            server_dir = Path(__file__).parent.parent.parent  # shinka -> webui -> visualization.py

            print(f"[SERVER] Launching evolution with command:")
            print(f"[SERVER]   server_dir (cwd): {server_dir}")
            print(f"[SERVER]   target workspace: {workspace_path}")
            print(f"[SERVER]   results dir: {target_results_dir}")
            print(f"[SERVER]   cmd: {cmd_str}")

            # Create results directory in the TARGET workspace
            target_results_dir.mkdir(parents=True, exist_ok=True)
            log_file = target_results_dir / "evolution_launch.log"

            with open(log_file, 'w') as log_f:
                log_f.write(f"Command: {cmd_str}\n")
                log_f.write(f"Server directory: {server_dir}\n")
                log_f.write(f"Target workspace: {workspace_path}\n")
                log_f.write("=" * 80 + "\n")

            # Clean environment - remove VIRTUAL_ENV so uv uses project's venv
            clean_env = os.environ.copy()
            # Keep API keys but let uv find the right venv

            # Start subprocess with output going to log file
            with open(log_file, 'a') as log_f:
                process = subprocess.Popen(
                    cmd_parts,
                    cwd=str(server_dir),  # Run from server dir so uv finds shinka
                    stdout=log_f,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,  # Detach from parent process
                    env=clean_env,
                )

            print(f"[SERVER] Evolution process started with PID: {process.pid}")
            print(f"[SERVER] Log file: {log_file}")

            # Wait briefly to detect immediate crashes
            import time
            time.sleep(2)

            # Check if process is still running
            poll_result = process.poll()
            if poll_result is not None:
                # Process already exited - likely crashed
                with open(log_file, 'r') as f:
                    log_content = f.read()
                # Extract error from log
                error_lines = [l for l in log_content.split('\n') if 'Error' in l or 'Exception' in l or 'Traceback' in l]
                error_summary = '\n'.join(error_lines[-5:]) if error_lines else "Process exited immediately"
                raise RuntimeError(f"Evolution process crashed (exit code {poll_result}): {error_summary}\n\nFull log: {log_file}")

            # Track this results directory so list_databases can find it
            # (even if it's outside the server's main results/ directory)
            launched_run_roots.add(str(target_results_dir))
            print(f"[SERVER] Tracking external results directory: {target_results_dir}")

            self.send_json_response({
                "ok": True,
                "run_id": run_name,
                "results_path": str(target_results_dir),
                "db_path": str(target_results_dir / db_config.db_path),
                "log_file": str(log_file),
                "message": f"Evolution run started successfully (PID: {process.pid})",
            })
        except Exception as e:
            print(f"[SERVER] Error starting evolution run: {e}")
            import traceback
            traceback.print_exc()
            self.send_json_response({"ok": False, "error": str(e)})

    # ========== End New Run Feature Handlers ==========

    def send_json_response(self, data):
        """Helper to send a JSON response."""
        # Use custom JSON encoder to handle NaN values
        payload = json.dumps(data, default=self._json_encoder).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _json_encoder(self, obj):
        """Custom JSON encoder to handle NaN and Inf values."""
        import math

        if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
            return None
        raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def create_handler_factory(search_root):
    """Create a handler factory that passes the search root to handler."""

    def handler_factory(*args, **kwargs):
        return DatabaseRequestHandler(*args, search_root=search_root, **kwargs)

    return handler_factory


def start_server(port: int, search_root: str, db_path: Optional[str] = None):
    """Start the HTTP server."""
    # Change to the webui directory inside the shinka package to serve static files
    webui_dir = os.path.dirname(__file__)
    webui_dir = os.path.abspath(webui_dir)

    if not os.path.exists(webui_dir):
        raise FileNotFoundError(f"Webui directory not found: {webui_dir}")

    os.chdir(webui_dir)
    print(f"[DEBUG] Server root directory: {webui_dir}")
    print(f"[DEBUG] Search root directory: {search_root}")

    # Create handler factory with search root
    handler_factory = create_handler_factory(search_root)

    # Reuse the socket so you can restart quickly
    class ReusableTCPServer(socketserver.TCPServer):
        allow_reuse_address = True

    with ReusableTCPServer(("", port), handler_factory) as httpd:
        msg = f"\n[*] Serving http://0.0.0.0:{port}  (Ctrl+C to stop)"
        print(msg)
        httpd.serve_forever()


def main():
    """Main entry point for shinka_visualize command."""
    description = "Serve the Shinka visualization UI for evolution results."
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        "root_directory",
        nargs="?",
        default=os.getcwd(),
        help=(
            "Root directory to search for database files "
            "(default: current working directory)"
        ),
    )
    parser.add_argument(
        "-p",
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help="Port to listen on (default: 8000).",
    )
    parser.add_argument(
        "--open",
        dest="open_browser",
        action="store_true",
        help="Open browser on the local machine (if DISPLAY is set)",
    )
    parser.add_argument(
        "--db",
        type=str,
        default=None,
        help="Path to a specific database file to serve.",
    )
    args = parser.parse_args()

    # Resolve the root directory to an absolute path
    search_root = os.path.abspath(args.root_directory)

    if not os.path.exists(search_root):
        print(f"Error: Root directory does not exist: {search_root}")
        sys.exit(1)

    print(f"[INFO] Searching for databases in: {search_root}")

    # Kick off the HTTP server in a daemon thread.
    server_thread = threading.Thread(
        target=start_server,
        args=(args.port, search_root, args.db),
        daemon=True,
    )
    server_thread.start()
    time.sleep(0.8)  # tiny delay so the banner prints before we continue

    # Construct URL, passing db path if provided
    base_url = f"http://localhost:{args.port}/viz_tree.html"
    if args.db:
        # URL encode the db path to handle special characters
        url_params = urllib.parse.urlencode({"db_path": args.db})
        viz_url = f"{base_url}?{url_params}"
    else:
        viz_url = base_url

    # Try to open a browser if requested
    if args.open_browser:
        try:
            webbrowser.open_new_tab(viz_url)
            print(f"→ Opening {viz_url} in browser")
        except Exception as e:
            print(f"→ Could not open browser automatically: {e}")
            print(f"→ Visit {viz_url}")
    else:
        print(f"→ Visit {viz_url}")
        print("(remember to forward the port if this is a remote host)")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[*] Shutting down.")


if __name__ == "__main__":
    main()
