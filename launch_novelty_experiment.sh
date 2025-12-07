#!/bin/bash
# Novelty A/B Test Experiment Launcher
# 6 runs: 3 backends (Codex, Gemini, Claude) x 2 modes (baseline, novelty)

cd /Users/juno/workspace/shrinkaevolve
source .venv/bin/activate

echo "=== Launching Novelty A/B Test Experiment ==="
echo "Date: $(date)"
echo ""

# Common base args
BASE="task=circle_packing variant=circle_packing_example evolution=large_budget evo_config.num_generations=50"
AGENTIC="+evo_config.agentic_mode=true"

# 1. Codex Baseline (no novelty)
echo "[1/6] Launching Codex Baseline..."
nohup uv run shinka_launch $BASE exp_name=codex_basic_50gen \
    $AGENTIC \
    +evo_config.agentic.backend=codex \
    +evo_config.agentic.extra_cli_config.model=gpt-5.1-codex-mini \
    evo_config.embedding_model=null \
    > /tmp/codex_basic.log 2>&1 &
echo "  PID: $!"
sleep 2

# 2. Codex Novelty
echo "[2/6] Launching Codex Novelty..."
nohup uv run shinka_launch $BASE exp_name=codex_novelty_50gen \
    $AGENTIC \
    +evo_config.agentic.backend=codex \
    +evo_config.agentic.extra_cli_config.model=gpt-5.1-codex-mini \
    evo_config.max_novelty_attempts=5 \
    evo_config.code_embed_sim_threshold=0.95 \
    evo_config.embedding_model=text-embedding-3-small \
    > /tmp/codex_novelty.log 2>&1 &
echo "  PID: $!"
sleep 2

# 3. Gemini Baseline (no novelty)
echo "[3/6] Launching Gemini Baseline..."
nohup uv run shinka_launch $BASE exp_name=gemini_basic_50gen \
    $AGENTIC \
    +evo_config.agentic.backend=gemini \
    +evo_config.agentic.extra_cli_config.model=gemini-3-pro-preview \
    evo_config.embedding_model=null \
    > /tmp/gemini_basic.log 2>&1 &
echo "  PID: $!"
sleep 2

# 4. Gemini Novelty
echo "[4/6] Launching Gemini Novelty..."
nohup uv run shinka_launch $BASE exp_name=gemini_novelty_50gen \
    $AGENTIC \
    +evo_config.agentic.backend=gemini \
    +evo_config.agentic.extra_cli_config.model=gemini-3-pro-preview \
    evo_config.max_novelty_attempts=5 \
    evo_config.code_embed_sim_threshold=0.95 \
    evo_config.embedding_model=text-embedding-3-small \
    > /tmp/gemini_novelty.log 2>&1 &
echo "  PID: $!"
sleep 2

# 5. Claude Baseline (no novelty)
echo "[5/6] Launching Claude Baseline..."
nohup uv run shinka_launch $BASE exp_name=claude_basic_50gen \
    $AGENTIC \
    +evo_config.agentic.backend=claude \
    evo_config.embedding_model=null \
    > /tmp/claude_basic.log 2>&1 &
echo "  PID: $!"
sleep 2

# 6. Claude Novelty
echo "[6/6] Launching Claude Novelty..."
nohup uv run shinka_launch $BASE exp_name=claude_novelty_50gen \
    $AGENTIC \
    +evo_config.agentic.backend=claude \
    evo_config.max_novelty_attempts=5 \
    evo_config.code_embed_sim_threshold=0.95 \
    evo_config.embedding_model=text-embedding-3-small \
    > /tmp/claude_novelty.log 2>&1 &
echo "  PID: $!"

echo ""
echo "=== All 6 experiments launched ==="
echo "Monitor with: tail -f /tmp/*.log"
echo "Check progress: ps aux | grep shinka_launch"
