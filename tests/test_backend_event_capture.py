#!/usr/bin/env python3
"""
Test script to capture and compare events from each backend.

Run with: uv run python tests/test_backend_event_capture.py [backend]
Where backend is: codex, gemini, claude, shinka

This creates a minimal task and captures ALL events emitted by each backend
to understand what data we get from each.

NOTE: This is a manual test script, not meant for pytest collection.
      Run directly with python, not via pytest.
"""

import json
import os
import sys
import tempfile
from pathlib import Path
from datetime import datetime

import pytest

# Skip this entire module when run via pytest
# This is a manual test script meant to be run directly
pytestmark = pytest.mark.skip(
    reason="Manual test script - run directly with: uv run python tests/test_backend_event_capture.py [backend]"
)

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))


def create_test_workspace(tmpdir: Path) -> dict:
    """Create minimal test files."""
    main_py = tmpdir / "main.py"
    main_py.write_text("""# EVOLVE-BLOCK-START
def add(a, b):
    return a + b

def main():
    print(add(1, 2))

if __name__ == "__main__":
    main()
# EVOLVE-BLOCK-END
""")
    return {Path("main.py"): main_py.read_text()}


def test_backend(backend: str, tmpdir: Path) -> list:
    """Run a backend and capture all events."""
    from shinka.edit.agentic import AgenticEditor, AgentContext
    
    # Import the right runner
    if backend == "codex":
        from shinka.edit.codex_cli import run_codex_task as runner
    elif backend == "gemini":
        from shinka.edit.gemini_cli import run_gemini_task as runner
    elif backend == "claude":
        from shinka.edit.claude_cli import run_claude_task as runner
    elif backend == "shinka":
        from shinka.edit.shinka_agent import run_shinka_task as runner
    else:
        raise ValueError(f"Unknown backend: {backend}")
    
    # Simple test prompt
    user_prompt = """# Task Context

This is a simple test. The current code adds two numbers.

# Current program

```python
def add(a, b):
    return a + b
```

Current score: 0.5

# Task

Make a tiny change - just add a docstring to the add function. 
Then output the required NAME/DESCRIPTION/SUMMARY format.
"""

    system_prompt = """You are in a sandbox. Make edits using shell commands.
When done, output:
<NAME>add_docstring</NAME>
<DESCRIPTION>Added docstring</DESCRIPTION>
<SUMMARY>- main.py: added docstring</SUMMARY>
"""

    base_files = create_test_workspace(tmpdir)
    
    # Capture all events
    events = []
    
    print(f"\n{'='*60}")
    print(f"Testing {backend.upper()} backend")
    print(f"{'='*60}")
    print(f"Workspace: {tmpdir}")
    print(f"Starting at: {datetime.now().isoformat()}")
    print()
    
    try:
        for event in runner(
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            workdir=tmpdir,
            profile=None,
            sandbox="none",
            approval_mode="auto-edit",
            extra_cli_config={},
            max_seconds=120,  # 2 min timeout
            max_events=50,
        ):
            events.append(event)
            event_type = event.get("type", "unknown")
            
            # Print summary of each event
            if event_type == "init":
                print(f"  [{event_type}] session_id={event.get('session_id', 'N/A')[:8]}... model={event.get('model', 'N/A')}")
            elif event_type == "tool_use":
                print(f"  [{event_type}] {event.get('tool_name', 'N/A')}({json.dumps(event.get('parameters', {}))[:50]}...)")
            elif event_type == "command_execution":
                item = event.get("item", {})
                cmd = item.get("command", "")[:40]
                status = item.get("status", "")
                print(f"  [{event_type}] {cmd}... -> {status}")
            elif event_type == "agent_message":
                item = event.get("item", {})
                text = item.get("text", "")[:60].replace("\n", " ")
                print(f"  [{event_type}] {text}...")
            elif event_type == "message":
                role = event.get("role", "")
                content = str(event.get("content", ""))[:60].replace("\n", " ")
                print(f"  [{event_type}] role={role}: {content}...")
            elif event_type == "thinking":
                text = event.get("text", "")[:60].replace("\n", " ")
                print(f"  [{event_type}] {text}...")
            elif event_type == "usage":
                usage = event.get("usage", {})
                print(f"  [{event_type}] in={usage.get('input_tokens', 0)} out={usage.get('output_tokens', 0)}")
            else:
                print(f"  [{event_type}] {json.dumps(event)[:80]}...")
                
    except Exception as e:
        print(f"  [ERROR] {type(e).__name__}: {e}")
        events.append({"type": "error", "error": str(e)})
    
    print(f"\nFinished at: {datetime.now().isoformat()}")
    return events


def analyze_events(events: list, backend: str):
    """Analyze captured events."""
    print(f"\n{'='*60}")
    print(f"Event Analysis for {backend.upper()}")
    print(f"{'='*60}")
    
    # Count event types
    type_counts = {}
    for e in events:
        t = e.get("type", "unknown")
        type_counts[t] = type_counts.get(t, 0) + 1
    
    print("\nEvent type counts:")
    for t, count in sorted(type_counts.items()):
        print(f"  {t}: {count}")
    
    # Check what we capture
    has_init = any(e.get("type") == "init" for e in events)
    has_thinking = any(e.get("type") == "thinking" for e in events)
    has_message = any(e.get("type") == "message" for e in events)
    has_agent_message = any(e.get("type") == "agent_message" for e in events)
    has_tool_use = any(e.get("type") == "tool_use" for e in events)
    has_usage = any(e.get("type") == "usage" for e in events)
    
    print("\nCapabilities:")
    print(f"  ✓ init (session start): {has_init}")
    print(f"  {'✓' if has_thinking else '✗'} thinking/reasoning: {has_thinking}")
    print(f"  {'✓' if has_message else '✗'} message (raw): {has_message}")
    print(f"  {'✓' if has_agent_message else '✗'} agent_message (final): {has_agent_message}")
    print(f"  {'✓' if has_tool_use else '✗'} tool_use: {has_tool_use}")
    print(f"  {'✓' if has_usage else '✗'} usage (tokens): {has_usage}")
    
    # Save full events to file for inspection
    output_file = Path(f"/tmp/backend_events_{backend}.json")
    with open(output_file, "w") as f:
        json.dump(events, f, indent=2, default=str)
    print(f"\nFull events saved to: {output_file}")
    
    return type_counts


def main():
    backends = sys.argv[1:] if len(sys.argv) > 1 else ["gemini"]
    
    all_results = {}
    
    for backend in backends:
        with tempfile.TemporaryDirectory(prefix=f"shinka_test_{backend}_") as tmpdir:
            tmpdir = Path(tmpdir)
            events = test_backend(backend, tmpdir)
            all_results[backend] = analyze_events(events, backend)
    
    # Compare if multiple backends
    if len(all_results) > 1:
        print(f"\n{'='*60}")
        print("COMPARISON")
        print(f"{'='*60}")
        
        all_types = set()
        for counts in all_results.values():
            all_types.update(counts.keys())
        
        print(f"\n{'Event Type':<25} " + " ".join(f"{b:>10}" for b in all_results.keys()))
        print("-" * (25 + 11 * len(all_results)))
        for t in sorted(all_types):
            counts = [str(all_results[b].get(t, 0)) for b in all_results.keys()]
            print(f"{t:<25} " + " ".join(f"{c:>10}" for c in counts))


if __name__ == "__main__":
    main()
