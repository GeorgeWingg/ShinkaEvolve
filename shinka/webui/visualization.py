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

# We'll use a simple text-to-PDF approach instead of complex dependencies
WEASYPRINT_AVAILABLE = False

DEFAULT_PORT = 8000
CACHE_EXPIRATION_SECONDS = 5  # Cache data for 5 seconds
db_cache: Dict[str, Tuple[float, Any]] = {}


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

        if path == "/api/credentials":
            return self.handle_credentials_get()

        if path == "/api/evolution_runs":
            return self.handle_evolution_runs()

        if path == "/api/active_jobs" and "db_path" in query:
            return self.handle_active_jobs(query)

        if path == "/api/session_state" and "session_id" in query:
            return self.handle_session_state(query)

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

        if path == "/api/credentials":
            return self.handle_credentials_post()

        # Return 404 for unknown POST endpoints
        self.send_error(404, f"Unknown POST endpoint: {path}")

    def handle_evolution_run_delete(self):
        """Delete an evolution run directory."""
        import shutil

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

            # Resolve the path and validate it's under search_root
            run_path = os.path.abspath(run_dir)
            search_root = os.path.abspath(self.search_root)

            # Security check: ensure the path is under search_root/results
            if not run_path.startswith(search_root):
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

            # Check if there's a shinka.pid indicating an active run
            pid_file = os.path.join(run_path, "shinka.pid")
            if os.path.exists(pid_file):
                try:
                    with open(pid_file, "r") as f:
                        pid = int(f.read().strip())
                    # Check if process is still running
                    os.kill(pid, 0)  # Signal 0 just checks if process exists
                    self.send_json_response({
                        "ok": False,
                        "error": f"Cannot delete: run is still active (PID {pid})"
                    })
                    return
                except (ValueError, ProcessLookupError, PermissionError):
                    # PID file is stale or process is not running
                    pass

            # Delete the directory
            print(f"[SERVER] Deleting directory: {run_path}")
            shutil.rmtree(run_path)

            self.send_json_response({"ok": True})
            print(f"[SERVER] Successfully deleted: {run_path}")

        except json.JSONDecodeError as e:
            self.send_json_response({"ok": False, "error": f"Invalid JSON: {e}"})
        except Exception as e:
            print(f"[SERVER] Error deleting run: {e}")
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
            # Get the actual db path relative to search root
            actual_db_path = self._get_actual_db_path(db_path)
            run_dir = os.path.dirname(os.path.join(self.search_root, actual_db_path))
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
        """Scan the search root directory for .db files."""
        print(
            f"[SERVER] Received request for database list, "
            f"searching in: {self.search_root}"
        )
        db_files = []
        date_pattern = re.compile(r"_(\d{8}_\d{6})")

        # Get the task name from the search root directory name
        task_name = os.path.basename(self.search_root)

        if os.path.exists(self.search_root):
            print(f"[SERVER] Scanning for .db files in: {self.search_root}")
            for root, _, files in os.walk(self.search_root):
                for f in files:
                    if f.lower().endswith((".db", ".sqlite")):
                        full_path = os.path.join(root, f)
                        client_path = os.path.relpath(full_path, self.search_root)
                        display_name = f"{Path(f).stem} - {Path(client_path).parent}"

                        # Extract date for sorting
                        sort_key = "0"  # Default for paths without a date
                        match = date_pattern.search(client_path)
                        if match:
                            sort_key = match.group(1)

                        # Modify the path structure to include task name for proper organization
                        # If the path doesn't already have 3+ parts, prepend the task name
                        path_parts = client_path.split("/")
                        if len(path_parts) < 3:
                            # Add task name as the first part of the path
                            modified_client_path = f"{task_name}/{client_path}"
                        else:
                            modified_client_path = client_path

                        db_info = {
                            "path": modified_client_path,
                            "name": display_name,
                            "sort_key": sort_key,  # Add key for sorting
                            "actual_path": client_path,  # Keep the actual relative path for file operations
                        }
                        db_files.append(db_info)
                        print(
                            f"[SERVER] Found DB: {client_path} -> {modified_client_path} (sort: {sort_key})"
                        )

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
        """Convert a potentially modified db_path back to the actual file path."""
        task_name = os.path.basename(self.search_root)

        # If the path starts with the task name, remove it
        if db_path.startswith(f"{task_name}/"):
            return db_path[len(task_name) + 1 :]

        return db_path

    def handle_get_programs(self, db_path: str):
        """Fetch all programs from a given database file."""
        print(f"[SERVER] Fetching programs from DB: {db_path}")

        # Handle the case where db_path might have the task name prepended
        # Extract the actual path by removing the task name prefix if present
        actual_db_path = self._get_actual_db_path(db_path)

        # Check cache first
        if db_path in db_cache:
            last_fetch_time, cached_data = db_cache[db_path]
            if time.time() - last_fetch_time < CACHE_EXPIRATION_SECONDS:
                print(f"[SERVER] Serving from cache for DB: {db_path}")
                self.send_json_response(cached_data)
                return

        # Construct absolute path to the database from search root using actual path
        abs_db_path = os.path.join(self.search_root, actual_db_path)
        print(f"[SERVER] Absolute DB path: {abs_db_path} (from {db_path})")

        if not os.path.exists(abs_db_path):
            self.send_error(404, f"Database file not found: {actual_db_path}")
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
                programs_dict = [p.to_dict() for p in programs]

                # Update cache
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

    def handle_get_meta_files(self, db_path: str):
        """List available meta_{gen}.txt files for a given database."""
        print(f"[SERVER] Listing meta files for DB: {db_path}")

        # Get the actual database path
        actual_db_path = self._get_actual_db_path(db_path)

        # Get the directory containing the database file
        abs_db_path = os.path.join(self.search_root, actual_db_path)
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

        # Get the actual database path
        actual_db_path = self._get_actual_db_path(db_path)

        # Get the directory containing the database file
        abs_db_path = os.path.join(self.search_root, actual_db_path)
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

        # Get the actual database path
        actual_db_path = self._get_actual_db_path(db_path)

        # Get the directory containing the database file
        abs_db_path = os.path.join(self.search_root, actual_db_path)
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
