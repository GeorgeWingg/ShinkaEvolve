#!/bin/bash
# Launch 3 novelty experiments

cd /Users/juno/workspace/shrinkaevolve
source .venv/bin/activate

# Load environment variables
set -a
source .env
set +a

# Launch Codex Novelty
uv run shinka_launch task=circle_packing variant=circle_packing_example evolution=large_budget evo_config.num_generations=50 exp_name=codex_novelty_50gen +evo_config.agentic_mode=true +evo_config.agentic.backend=codex +evo_config.agentic.extra_cli_config.model=gpt-5.1-codex-mini ++evo_config.max_novelty_attempts=5 ++evo_config.code_embed_sim_threshold=0.95 ++evo_config.embedding_model=text-embedding-3-small > /tmp/codex_novelty.log 2>&1 &
CODEX_PID=$!
echo "Codex Novelty PID: $CODEX_PID"

# Launch Gemini Novelty
uv run shinka_launch task=circle_packing variant=circle_packing_example evolution=large_budget evo_config.num_generations=50 exp_name=gemini_novelty_50gen +evo_config.agentic_mode=true +evo_config.agentic.backend=gemini +evo_config.agentic.extra_cli_config.model=gemini-3-pro-preview ++evo_config.max_novelty_attempts=5 ++evo_config.code_embed_sim_threshold=0.95 ++evo_config.embedding_model=text-embedding-3-small > /tmp/gemini_novelty.log 2>&1 &
GEMINI_PID=$!
echo "Gemini Novelty PID: $GEMINI_PID"

# Launch Claude Novelty
uv run shinka_launch task=circle_packing variant=circle_packing_example evolution=large_budget evo_config.num_generations=50 exp_name=claude_novelty_50gen +evo_config.agentic_mode=true +evo_config.agentic.backend=claude ++evo_config.max_novelty_attempts=5 ++evo_config.code_embed_sim_threshold=0.95 ++evo_config.embedding_model=text-embedding-3-small > /tmp/claude_novelty.log 2>&1 &
CLAUDE_PID=$!
echo "Claude Novelty PID: $CLAUDE_PID"

# Save PIDs
echo "$CODEX_PID $GEMINI_PID $CLAUDE_PID" > /tmp/novelty_pids.txt
echo "All experiments launched. PIDs saved to /tmp/novelty_pids.txt"
