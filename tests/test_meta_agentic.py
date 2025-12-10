"""
Quick validation test for MetaSummarizer agentic mode parity.

Tests that:
1. MetaSummarizer can be instantiated with agent_runner
2. _query_via_agent() correctly parses events
3. construct_individual_program_msg() handles multi-file
4. The 3-step meta flow works with agent_runner
"""

import sys
from pathlib import Path

import pytest

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from shinka.core.summarizer import MetaSummarizer
from shinka.database import Program
from shinka.prompts import construct_individual_program_msg


def mock_agent_runner(user_prompt, workdir, **kwargs):
    """Fake agent runner that yields mock events."""
    # Simulate what a real CLI backend would return
    yield {
        "type": "init",
        "session_id": "mock-session-123",
        "model": "mock-model",
    }
    yield {
        "type": "agent_message",
        "item": {
            "type": "agent_message",
            "text": "## Analysis\n\nThis program demonstrates good practices.\n\n"
        }
    }
    yield {
        "type": "agent_message",
        "item": {
            "type": "agent_message",
            "text": "**Key observations:**\n- Clean code structure\n- Proper error handling\n"
        }
    }
    yield {
        "type": "usage",
        "usage": {"input_tokens": 100, "output_tokens": 50}
    }


@pytest.fixture
def meta():
    """Create a MetaSummarizer instance with mock agent_runner."""
    return MetaSummarizer(
        agent_runner=mock_agent_runner,
        language="python",
    )


def test_instantiation(meta):
    """Test that MetaSummarizer can be created with agent_runner."""
    assert meta.agent_runner is not None
    assert meta.meta_llm_client is None


def test_query_via_agent(meta):
    """Test that _query_via_agent correctly parses mock events."""
    response, cost = meta._query_via_agent("Test prompt", "Test system")

    assert response is not None, "Response should not be None"
    assert "Analysis" in response, f"Response should contain 'Analysis': {response}"
    assert "Key observations" in response, f"Response should contain 'Key observations': {response}"
    assert cost == 0.0, "Cost should be 0.0 (agent_runner doesn't track cost)"


def test_multi_file_program_msg():
    """Test that construct_individual_program_msg handles multi-file metadata."""
    # Create a program with multi-file content
    dummy_program = Program(
        id="test-multi-123",
        code="# fallback single file content",
        language="python",
        generation=1,
        combined_score=0.85,
        correct=True,
        public_metrics={"accuracy": 0.95, "speed": 1.2},
        metadata={
            "patch_name": "test_patch",
            "all_code": {
                "main.py": "def main():\n    print('hello')\n\nif __name__ == '__main__':\n    main()",
                "helper.py": "def helper():\n    return 42",
                "utils/math.py": "def add(a, b):\n    return a + b"
            }
        }
    )

    msg = construct_individual_program_msg(dummy_program, language="python")

    # Verify multi-file handling
    assert "Files (3 total)" in msg, f"Should show file count: {msg[:200]}"
    assert "main.py" in msg, "Should contain main.py"
    assert "helper.py" in msg, "Should contain helper.py"
    assert "utils/math.py" in msg, "Should contain utils/math.py"
    assert "def helper():" in msg, "Should contain helper.py content"
    assert "fallback single file" not in msg, "Should NOT use fallback single file"

    # Also test single-file fallback
    single_program = Program(
        id="test-single-123",
        code="def single(): pass",
        language="python",
        generation=1,
        combined_score=0.5,
        correct=False,
        public_metrics={},
        metadata={"patch_name": "single_patch"}
    )

    single_msg = construct_individual_program_msg(single_program)
    assert "def single(): pass" in single_msg, "Should contain single file code"
    assert "Files (" not in single_msg, "Should NOT have Files header for single file"


def test_meta_flow_with_mock(meta):
    """Test the full 3-step meta flow with mock agent_runner."""
    # Create dummy programs to analyze
    programs = [
        Program(
            id=f"prog-{i}",
            code=f"def func_{i}(): return {i}",
            language="python",
            generation=i,
            combined_score=0.5 + i * 0.1,
            correct=i % 2 == 0,
            public_metrics={"score": 0.5 + i * 0.1},
            metadata={"patch_name": f"patch_{i}"}
        )
        for i in range(3)
    ]

    # Add programs to meta tracking
    for prog in programs:
        meta.add_evaluated_program(prog)

    assert len(meta.evaluated_since_last_meta) == 3

    # Run the meta update
    recommendations, total_cost = meta.update_meta_memory()

    # Verify results
    assert recommendations is not None, "Should get recommendations"
    assert len(recommendations) > 0, "Recommendations should not be empty"
    assert total_cost == 0.0, "Cost should be 0.0 with mock"

    # Verify internal state was updated
    assert meta.meta_summary is not None, "meta_summary should be set"
    assert meta.meta_scratch_pad is not None, "meta_scratch_pad should be set"
    assert meta.meta_recommendations is not None, "meta_recommendations should be set"
    assert len(meta.evaluated_since_last_meta) == 0, "Should clear evaluated programs after processing"


def test_should_update_meta():
    """Test that should_update_meta works with agent_runner."""
    # With agent_runner, should allow updates
    meta_with_runner = MetaSummarizer(agent_runner=mock_agent_runner)
    meta_with_runner.add_evaluated_program(Program(
        id="test", code="x", language="python", generation=1,
        combined_score=0.5, correct=True, public_metrics={}, metadata={"patch_name": "test"}
    ))

    assert meta_with_runner.should_update_meta(1) == True, "Should allow update with agent_runner"

    # Without either, should not allow updates
    meta_without = MetaSummarizer()
    meta_without.add_evaluated_program(Program(
        id="test", code="x", language="python", generation=1,
        combined_score=0.5, correct=True, public_metrics={}, metadata={"patch_name": "test"}
    ))

    assert meta_without.should_update_meta(1) == False, "Should not allow update without agent_runner or llm_client"


# Keep main() for backwards compatibility with direct script execution
def main():
    """Run tests manually (for backwards compatibility)."""
    import subprocess
    import sys
    result = subprocess.run([sys.executable, "-m", "pytest", __file__, "-v"], check=False)
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
