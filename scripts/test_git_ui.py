#!/usr/bin/env python3
"""Generate test data for git-backed evolution UI verification.

Creates a minimal evolution database with git-backed storage to test:
- Provision Worktree button visibility and functionality
- Git Export button visibility and functionality
"""

import json
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from shinka.webui.git_worktree import EvolutionGitManager


def create_test_evolution(results_dir: Path) -> Path:
    """Create a test evolution with git-backed storage."""

    results_dir.mkdir(parents=True, exist_ok=True)

    # Create paths
    db_path = results_dir / "evolution_db.sqlite"
    git_repo_path = results_dir / "evolution.git"

    # Initialize git manager
    print(f"Creating git repo at {git_repo_path}")
    manager = EvolutionGitManager(git_repo_path, create=True)

    # Create seed workspace
    seed_dir = Path(tempfile.mkdtemp())
    (seed_dir / "main.py").write_text('''"""Circle packing solver - Generation 0"""

def solve():
    """Initial solution."""
    return [(0.5, 0.5, 0.3)]

if __name__ == "__main__":
    circles = solve()
    print(f"Found {len(circles)} circles")
''')
    (seed_dir / "helpers.py").write_text('''"""Helper functions."""

def compute_overlap(c1, c2):
    """Check if two circles overlap."""
    dx = c1[0] - c2[0]
    dy = c1[1] - c2[1]
    dist = (dx**2 + dy**2) ** 0.5
    return dist < c1[2] + c2[2]
''')

    # Create initial commit
    node0_uuid = str(uuid.uuid4())
    sha0 = manager.init_from_workspace(seed_dir, "Initial seed", node0_uuid)
    print(f"Created seed commit: {sha0[:8]} (node: {node0_uuid[:8]})")

    # Create mutation 1
    node1_uuid = str(uuid.uuid4())
    with manager.mutation_context(sha0, node1_uuid) as worktree:
        (worktree.path / "main.py").write_text('''"""Circle packing solver - Generation 1"""

def solve():
    """Improved solution with more circles."""
    return [
        (0.3, 0.3, 0.2),
        (0.7, 0.3, 0.2),
        (0.5, 0.7, 0.25),
    ]

if __name__ == "__main__":
    circles = solve()
    total_area = sum(3.14159 * r**2 for x, y, r in circles)
    print(f"Found {len(circles)} circles, total area: {total_area:.4f}")
''')
        sha1 = manager.commit_mutation(worktree, "Gen 1: Add more circles", node1_uuid)
    print(f"Created gen 1 commit: {sha1[:8]} (node: {node1_uuid[:8]})")

    # Create mutation 2 (branch from sha0)
    node2_uuid = str(uuid.uuid4())
    with manager.mutation_context(sha0, node2_uuid) as worktree:
        (worktree.path / "main.py").write_text('''"""Circle packing solver - Generation 1 (alt)"""

def solve():
    """Alternative approach with larger circles."""
    return [
        (0.5, 0.5, 0.4),
        (0.2, 0.2, 0.15),
    ]

if __name__ == "__main__":
    circles = solve()
    total_area = sum(3.14159 * r**2 for x, y, r in circles)
    print(f"Found {len(circles)} circles, total area: {total_area:.4f}")
''')
        sha2 = manager.commit_mutation(worktree, "Gen 1 alt: Larger circles", node2_uuid)
    print(f"Created gen 1 alt commit: {sha2[:8]} (node: {node2_uuid[:8]})")

    # Create SQLite database
    print(f"Creating database at {db_path}")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Create tables with full schema
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS programs (
            id TEXT PRIMARY KEY,
            code TEXT NOT NULL,
            language TEXT NOT NULL,
            parent_id TEXT,
            archive_inspiration_ids TEXT,
            top_k_inspiration_ids TEXT,
            generation INTEGER NOT NULL,
            timestamp REAL NOT NULL,
            code_diff TEXT,
            combined_score REAL,
            public_metrics TEXT,
            private_metrics TEXT,
            text_feedback TEXT,
            human_rating REAL,
            complexity REAL,
            embedding TEXT,
            embedding_pca_2d TEXT,
            embedding_pca_3d TEXT,
            embedding_cluster_id INTEGER,
            correct BOOLEAN DEFAULT 0,
            children_count INTEGER NOT NULL DEFAULT 0,
            metadata TEXT,
            migration_history TEXT,
            island_idx INTEGER
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS archive (
            program_id TEXT PRIMARY KEY,
            FOREIGN KEY (program_id) REFERENCES programs(id) ON DELETE CASCADE
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS metadata_store (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')

    # Insert run metadata
    cursor.execute("INSERT INTO metadata_store VALUES (?, ?)",
                   ("git_backed_storage", "true"))
    cursor.execute("INSERT INTO metadata_store VALUES (?, ?)",
                   ("git_repo_path", str(git_repo_path)))
    cursor.execute("INSERT INTO metadata_store VALUES (?, ?)",
                   ("task_name", "git_test"))

    now = time.time()

    # Insert programs with full schema
    programs = [
        # (id, code, language, parent_id, archive_inspiration_ids, top_k_inspiration_ids,
        #  generation, timestamp, code_diff, combined_score, public_metrics, private_metrics,
        #  text_feedback, human_rating, complexity, embedding, embedding_pca_2d, embedding_pca_3d,
        #  embedding_cluster_id, correct, children_count, metadata, migration_history, island_idx)
        (node0_uuid, "", "python", None, "[]", "[]", 0, now, None, 0.5,
         json.dumps({"score": 0.5}), "{}", None, None, None, None, None, None, None, 0, 2,
         json.dumps({"git_commit_sha": sha0}), None, 0),
        (node1_uuid, "", "python", node0_uuid, "[]", "[]", 1, now + 1, None, 0.75,
         json.dumps({"score": 0.75}), "{}", None, None, None, None, None, None, None, 0, 0,
         json.dumps({"git_commit_sha": sha1}), None, 0),
        (node2_uuid, "", "python", node0_uuid, "[]", "[]", 1, now + 2, None, 0.65,
         json.dumps({"score": 0.65}), "{}", None, None, None, None, None, None, None, 0, 0,
         json.dumps({"git_commit_sha": sha2}), None, 0),
    ]

    cursor.executemany('''
        INSERT INTO programs (id, code, language, parent_id, archive_inspiration_ids, top_k_inspiration_ids,
            generation, timestamp, code_diff, combined_score, public_metrics, private_metrics,
            text_feedback, human_rating, complexity, embedding, embedding_pca_2d, embedding_pca_3d,
            embedding_cluster_id, correct, children_count, metadata, migration_history, island_idx)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', programs)

    conn.commit()
    conn.close()

    print(f"\nTest evolution created successfully!")
    print(f"  Database: {db_path}")
    print(f"  Git repo: {git_repo_path}")
    print(f"  Nodes: {len(programs)}")
    print(f"\nTo view in UI:")
    print(f"  1. Open http://localhost:8888")
    print(f"  2. Select task 'git_test'")
    print(f"  3. Select the result")
    print(f"  4. Click a node to see 'Provision Worktree' button")
    print(f"  5. Look for 'Git Export' button in the toolbar")

    return db_path


if __name__ == "__main__":
    # Create in results directory
    results_base = Path(__file__).parent.parent / "results" / "git_test"
    timestamp = time.strftime("%Y.%m.%d_%H%M%S")
    results_dir = results_base / timestamp

    create_test_evolution(results_dir)
