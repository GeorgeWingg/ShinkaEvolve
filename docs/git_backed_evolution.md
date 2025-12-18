# Git-Backed Evolution Storage

This document explains how to use git-backed storage for evolution runs, enabling real git history for your evolutionary code changes.

## Overview

By default, ShinkaEvolve stores code as text blobs in SQLite. With git-backed storage enabled, each mutation becomes a real git commit in a dedicated repository, providing:

- **Real git history**: Each evolutionary step creates an actual git commit
- **Cherry-picking between branches**: Use standard git tools to combine improvements from different evolutionary paths
- **Export as git repository**: Download the entire evolution as a git repo that can be pushed to GitHub
- **Workspace provisioning**: Click a node in the visualization to create a working directory with that node's code

## Quick Start

### Enable Git-Backed Storage

In your Hydra config or command line:

```bash
uv run shinka_launch variant=your_variant \
  +evo_config.git_backed_storage=true
```

Or in a config file:

```yaml
# configs/evolution/git_backed.yaml
defaults:
  - agentic

evo_config:
  git_backed_storage: true
  # Optional: custom repo location (defaults to results/<task>/<run>/evolution.git)
  # git_repo_path: /path/to/custom/evolution.git
```

### View Evolution as Git History

After a run completes, you can view the full evolutionary tree:

```bash
# Navigate to the results directory
cd results/shinka_circle_packing/2025.12.14_123456/

# View the git history graph
git --git-dir=evolution.git log --oneline --graph --all
```

### Export Evolution for Sharing

From the WebUI:

1. Open the visualization for your run
2. Click the "Git Export" button in the file selector bar
3. Download the zip file containing the complete git repository
4. Extract and explore:

```bash
unzip evolution_export.zip -d evolution_export
cd evolution_export
git log --oneline --graph --all
```

### Provision a Worktree for Development

From the WebUI:

1. Select a high-performing node in the evolution tree
2. Click "Provision Worktree" in the node summary panel
3. The server creates a working directory at the displayed path
4. SSH into the server (if remote) and develop from that worktree

## How It Works

### Integration Architecture

When `git_backed_storage=true`, the evolution runner:

1. **Generation 0 (Seed)**: Calls `EvolutionGitManager.init_from_workspace()` to create the initial commit
2. **Subsequent Generations**: Calls `_git_commit_generation()` in `_finalize_job()` to commit each mutation
3. **Metadata Storage**: Stores `git_commit_sha` in each program's metadata for reference
4. **Lock Ordering**: Git operations complete before database writes to ensure consistency

This hybrid approach maintains both:
- **SQLite**: Stores metadata, scores, embeddings (fast querying)
- **evolution.git**: Stores actual code as git commits (full history)

### Repository Structure

When git-backed storage is enabled, ShinkaEvolve creates:

```
results/<task>/<timestamp>/
  evolution_db.sqlite     # Metadata, scores, embeddings (unchanged)
  evolution.git/          # Bare git repository with all code
    refs/shinka/nodes/    # One ref per evolutionary node
      <node-uuid-1>       # Points to commit SHA
      <node-uuid-2>
      ...
```

### Node References

Every evolutionary node gets a git ref at `refs/shinka/nodes/<node-uuid>`. These refs:

1. **Prevent garbage collection**: Commits are never cleaned up
2. **Enable full tree visualization**: `git log --graph --all` shows the complete tree
3. **Allow direct checkout**: `git checkout refs/shinka/nodes/<uuid>`

### Data Access Layer

The `Program` class provides a unified interface for both storage modes:

```python
from shinka.database.dbase import Program
from shinka.webui.git_worktree import EvolutionGitManager

# Load a program from the database
program = db.get_program(node_id)

# Get code content (works with both legacy blob and git-backed)
manager = EvolutionGitManager(repo_path) if git_backed else None
code = program.get_code_content(git_manager=manager)

# For git-backed programs, you can also:
sha = program.get_commit_sha()  # Get the commit SHA
```

## Configuration Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `git_backed_storage` | bool | `false` | Enable git-backed storage |
| `git_repo_path` | str | auto | Override repository location |

### Full Config Example

```yaml
evo_config:
  git_backed_storage: true

  # Agentic mode settings
  agentic_mode: true
  agentic:
    backend: codex
    max_turns: 10
```

## API Reference

### EvolutionGitManager

The main class for git operations:

```python
from shinka.webui.git_worktree import EvolutionGitManager

# Initialize (creates bare repo if doesn't exist)
manager = EvolutionGitManager(
    repo_path=Path("results/task/run/evolution.git"),
    create=True,
)

# Initialize with seed files
initial_sha = manager.init_from_workspace(
    workspace_path=Path("seed_files/"),
    message="Initial seed",
    node_uuid="node-0",
)

# Perform a mutation
with manager.mutation_context(parent_sha, "node-1") as worktree:
    # worktree.path is a working directory
    # Agent makes changes here...
    new_sha = manager.commit_mutation(worktree, "Generation 1", "node-1")
# Worktree automatically cleaned up

# Export for user download
manager.export_as_repo(Path("/tmp/evolution_export"))

# List all nodes
nodes = manager.list_all_nodes()
# Returns: [{"node_uuid": "...", "commit_sha": "..."}, ...]
```

### WebUI Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/node/provision_worktree` | POST | Create worktree for a node |
| `/api/run/export_git` | POST | Export evolution as git repo |
| `/download/exports/{filename}` | GET | Download exported zip |

## Cross-Platform Support

| Feature | Linux | macOS | Windows |
|---------|-------|-------|---------|
| Git worktrees | Native | Native | Requires Developer Mode* |
| File locking | fcntl | fcntl | Windows native locks |
| Temp directory | /tmp | /tmp or $TMPDIR | %TEMP% |

*On Windows without Developer Mode, git worktrees fall back to `--shared` clone which is slightly slower but fully functional.

## Best Practices

### When to Use Git-Backed Storage

**Recommended for:**
- Long evolution runs where you want to explore the history
- Collaborative projects where you'll share evolution results
- Development workflows where you want to continue from a specific node
- Large codebases where you want proper diff tracking

**Not necessary for:**
- Quick experiments where you only care about the final result
- Benchmarking runs focused on scores, not code review
- High-throughput runs where disk I/O is a bottleneck

### Working with Remote Servers

When running ShinkaEvolve on a remote server:

1. Use **"Git Export"** to download the repository as a zip file
2. Extract locally and explore with your favorite git tools
3. If you need to develop on the server, use **"Provision Worktree"** and SSH in

### Combining Evolutionary Branches

After export, you can use standard git operations:

```bash
# View all evolutionary paths
git log --oneline --graph --all

# Create a branch from a high-performing node
git checkout -b my-improvement refs/shinka/nodes/<node-uuid>

# Cherry-pick improvements from another path
git cherry-pick refs/shinka/nodes/<other-node-uuid>

# Merge multiple evolutionary discoveries
git merge refs/shinka/nodes/<another-node-uuid>
```

## Troubleshooting

### "No code available" Error

If you see this error when accessing a git-backed program:

1. Ensure the `evolution.git` directory exists
2. Verify the commit SHA is in the program's metadata
3. Check that the `EvolutionGitManager` is passed to `get_code_content()`

### "No git storage found" Error in WebUI

The WebUI supports two git storage modes:
- **evolution.git**: New git-backed runs with `git_backed_storage=true`
- **workspace/.git**: Legacy runs that committed to an isolated workspace

If neither exists, git features (provision worktree, export) won't be available. Check:
1. Was `git_backed_storage=true` set during the run?
2. For legacy runs, was workspace isolation enabled?

### Git Commit Failed During Evolution

If you see warnings like "Failed to create git commit for gen N":
- The evolution continues normally (graceful degradation to blob-only)
- Check disk space and permissions on the results directory
- Verify git is installed and accessible

### Worktree Creation Fails on Windows

On Windows without Developer Mode:
- Git worktrees require symlinks, which need elevated permissions
- The system automatically falls back to `--shared` clone
- To enable native worktrees, enable Developer Mode in Windows Settings

### Large Repository Performance

For very large evolutions (1000+ nodes):
- Export may take several minutes
- Consider using `git gc` on the exported repo to optimize
- The bare repo uses refs efficiently and shouldn't grow excessively

### Parent Commit Not Found

If mutations fail to find parent commits:
- Verify generation 0 completed successfully
- Check `_initial_git_sha` was set in the runner
- Parent program's metadata should have `git_commit_sha`

## See Also

- [GIT_WORKTREE_EXECPLAN.md](/GIT_WORKTREE_EXECPLAN.md) - Implementation details
- [WebUI Documentation](/docs/webui.md) - UI features
- [Configuration Guide](/docs/configuration.md) - Full config options
