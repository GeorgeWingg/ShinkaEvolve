#!/bin/bash
# LLM Review Tests for ShinkaEvolve
# Runs aspect-focused code reviews using `codex review`
#
# Usage:
#   ./run_reviews.sh              # Run all reviews
#   ./run_reviews.sh --security   # Run security reviews only
#   ./run_reviews.sh --diff-only  # Run diff-only reviews only

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
OUTPUT_DIR="$SCRIPT_DIR/results/${TIMESTAMP}"
mkdir -p "$OUTPUT_DIR"

# Parse arguments
RUN_SECURITY=true
RUN_ERROR=true
RUN_DIFF=true

if [[ "$1" == "--security" ]]; then
    RUN_ERROR=false
    RUN_DIFF=false
elif [[ "$1" == "--error" ]]; then
    RUN_SECURITY=false
    RUN_DIFF=false
elif [[ "$1" == "--diff-only" ]]; then
    RUN_SECURITY=false
    RUN_ERROR=false
fi

echo "=============================================="
echo "LLM Review Tests - ShinkaEvolve"
echo "Output: $OUTPUT_DIR"
echo "=============================================="

# ============================================
# FULL FILE REVIEWS (Security, Error Handling)
# These run on complete files, not just diffs
# ============================================

if $RUN_SECURITY; then
    echo ""
    echo "=== SECURITY REVIEWS (Full File) ==="

    echo "[S1] Subprocess injection..."
    codex review "Review shinka/edit/codex_cli.py, shinka/edit/claude_cli.py, shinka/edit/gemini_cli.py for command injection vulnerabilities in subprocess calls. Check shell=True usage, unsanitized inputs, path traversal. Provide prioritized findings with P0/P1/P2 severity." \
        --json > "$OUTPUT_DIR/S1_subprocess_injection.json" 2>&1 || echo "S1 failed"

    echo "[S2] API key exposure..."
    codex review "Review shinka/tools/credentials.py, shinka/webui/visualization.py for API key handling. Check for keys in logs, responses, error messages, or debug output. Provide prioritized findings with P0/P1/P2 severity." \
        --json > "$OUTPUT_DIR/S2_api_key_exposure.json" 2>&1 || echo "S2 failed"

    echo "[S3] Path traversal..."
    codex review "Review shinka/edit/agentic.py for path traversal vulnerabilities in scratch directory handling. Check that user-controlled paths cannot escape workspace boundaries. Provide prioritized findings with P0/P1/P2 severity." \
        --json > "$OUTPUT_DIR/S3_path_traversal.json" 2>&1 || echo "S3 failed"
fi

if $RUN_ERROR; then
    echo ""
    echo "=== ERROR HANDLING REVIEWS (Full File) ==="

    echo "[E1] Subprocess error handling..."
    codex review "Review shinka/edit/codex_cli.py, shinka/edit/claude_cli.py, shinka/edit/gemini_cli.py for proper subprocess error handling. Check for uncaught exceptions, timeout handling, zombie processes, proper cleanup on failure. Provide prioritized findings with P0/P1/P2 severity." \
        --json > "$OUTPUT_DIR/E1_subprocess_errors.json" 2>&1 || echo "E1 failed"

    echo "[E2] Database transaction safety..."
    codex review "Review shinka/database/dbase.py for transaction safety. Check for proper rollback on errors, connection leaks, concurrent access handling, and SQLite locking issues. Provide prioritized findings with P0/P1/P2 severity." \
        --json > "$OUTPUT_DIR/E2_database_safety.json" 2>&1 || echo "E2 failed"

    echo "[E3] WebUI API error responses..."
    codex review "Review shinka/webui/visualization.py for API error handling. Check that all Flask endpoints return proper error codes, don't leak stack traces, and handle malformed input gracefully. Provide prioritized findings with P0/P1/P2 severity." \
        --json > "$OUTPUT_DIR/E3_webui_errors.json" 2>&1 || echo "E3 failed"
fi

# ============================================
# DIFF-ONLY REVIEWS (Consistency, Resources, etc.)
# These review changes against main branch
# ============================================

if $RUN_DIFF; then
    echo ""
    echo "=== CONSISTENCY REVIEWS (Diff vs main) ==="

    echo "[C1] Backend interface parity..."
    codex review --base main "Review for backend interface consistency across shinka/edit/*.py. Check that all backends (codex_cli, claude_cli, gemini_cli, shinka_agent) return the same AgentResult structure and handle the same edge cases consistently. Provide prioritized findings with P0/P1/P2 severity." \
        --json > "$OUTPUT_DIR/C1_backend_parity.json" 2>&1 || echo "C1 failed"

    echo "[C2] Config schema consistency..."
    codex review --base main "Review for config schema consistency in configs/evolution/*.yaml. Check that agentic/evaluator config blocks use consistent keys across budget profiles (small, medium, large). Provide prioritized findings with P0/P1/P2 severity." \
        --json > "$OUTPUT_DIR/C2_config_schema.json" 2>&1 || echo "C2 failed"

    echo "[C3] Event/telemetry format consistency..."
    codex review --base main "Review shinka/edit/*.py for telemetry event consistency. Check that all backends emit comparable event types with consistent fields for session tracking. Provide prioritized findings with P0/P1/P2 severity." \
        --json > "$OUTPUT_DIR/C3_telemetry_format.json" 2>&1 || echo "C3 failed"

    echo ""
    echo "=== RESOURCE MANAGEMENT REVIEWS (Diff vs main) ==="

    echo "[R1] Process cleanup..."
    codex review --base main "Review shinka/edit/agentic.py and shinka/tools/codex_session_registry.py for process lifecycle management. Check for orphaned processes, PID file cleanup, proper signal handling. Provide prioritized findings with P0/P1/P2 severity." \
        --json > "$OUTPUT_DIR/R1_process_cleanup.json" 2>&1 || echo "R1 failed"

    echo ""
    echo "=== WEBUI REVIEWS (Diff vs main) ==="

    echo "[W1] XSS vulnerabilities..."
    codex review --base main "Review shinka/webui/viz_tree.html for XSS vulnerabilities. Check that user-controlled data is properly escaped before rendering in the DOM. Provide prioritized findings with P0/P1/P2 severity." \
        --json > "$OUTPUT_DIR/W1_xss.json" 2>&1 || echo "W1 failed"

    echo ""
    echo "=== LOGIC/CORRECTNESS REVIEWS (Diff vs main) ==="

    echo "[L1] Novelty judge correctness..."
    codex review --base main "Review shinka/core/novelty_judge.py for logical correctness. Check threshold comparisons, embedding similarity math, edge cases with empty corpus, and proper handling of None/NaN values. Provide prioritized findings with P0/P1/P2 severity." \
        --json > "$OUTPUT_DIR/L1_novelty_judge.json" 2>&1 || echo "L1 failed"
fi

# ============================================
# GENERATE SUMMARY
# ============================================

echo ""
echo "=== Generating Summary ==="

if [[ -f "$SCRIPT_DIR/parse_results.py" ]]; then
    python "$SCRIPT_DIR/parse_results.py" "$OUTPUT_DIR"
else
    echo "Warning: parse_results.py not found, skipping summary generation"
    echo "Results saved to: $OUTPUT_DIR"
    ls -la "$OUTPUT_DIR"
fi

echo ""
echo "=============================================="
echo "LLM Review Tests Complete"
echo "Results: $OUTPUT_DIR"
echo "=============================================="
