#!/bin/bash
# Launch 6 A/B experiments: 3 backends × 2 modes (baseline vs novelty)

cd /Users/juno/workspace/shrinkaevolve
export $(cat .env | xargs)

echo "=== Launching 6 experiments ==="

# 1. Codex Baseline (no novelty)
uv run shinka_launch \
    task=circle_packing variant=circle_packing_example evolution=large_budget \
    evo_config.num_generations=50 exp_name=codex_basic_50gen \
    +evo_config.agentic_mode=true +evo_config.agentic.backend=codex \
    +evo_config.agentic.extra_cli_config.model=gpt-5.1-codex-mini \
    evo_config.embedding_model=null \
    > /tmp/codex_basic.log 2>&1 &
echo "Codex Basic PID: $!"

# 2. Codex with Novelty
uv run shinka_launch \
    task=circle_packing variant=circle_packing_example evolution=large_budget \
    evo_config.num_generations=50 exp_name=codex_novelty_50gen \
    +evo_config.agentic_mode=true +evo_config.agentic.backend=codex \
    +evo_config.agentic.extra_cli_config.model=gpt-5.1-codex-mini \
    ++evo_config.max_novelty_attempts=5 \
    ++evo_config.code_embed_sim_threshold=0.95 \
    ++evo_config.embedding_model=text-embedding-3-small \
    > /tmp/codex_novelty.log 2>&1 &
echo "Codex Novelty PID: $!"

# 3. Gemini Baseline
uv run shinka_launch \
    task=circle_packing variant=circle_packing_example evolution=large_budget \
    evo_config.num_generations=50 exp_name=gemini_basic_50gen \
    +evo_config.agentic_mode=true +evo_config.agentic.backend=gemini \
    +evo_config.agentic.extra_cli_config.model=gemini-3-pro-preview \
    evo_config.embedding_model=null \
    > /tmp/gemini_basic.log 2>&1 &
echo "Gemini Basic PID: $!"

# 4. Gemini with Novelty
uv run shinka_launch \
    task=circle_packing variant=circle_packing_example evolution=large_budget \
    evo_config.num_generations=50 exp_name=gemini_novelty_50gen \
    +evo_config.agentic_mode=true +evo_config.agentic.backend=gemini \
    +evo_config.agentic.extra_cli_config.model=gemini-3-pro-preview \
    ++evo_config.max_novelty_attempts=5 \
    ++evo_config.code_embed_sim_threshold=0.95 \
    ++evo_config.embedding_model=text-embedding-3-small \
    > /tmp/gemini_novelty.log 2>&1 &
echo "Gemini Novelty PID: $!"

# 5. Claude Baseline
uv run shinka_launch \
    task=circle_packing variant=circle_packing_example evolution=large_budget \
    evo_config.num_generations=50 exp_name=claude_basic_50gen \
    +evo_config.agentic_mode=true +evo_config.agentic.backend=claude \
    evo_config.embedding_model=null \
    > /tmp/claude_basic.log 2>&1 &
echo "Claude Basic PID: $!"

# 6. Claude with Novelty
uv run shinka_launch \
    task=circle_packing variant=circle_packing_example evolution=large_budget \
    evo_config.num_generations=50 exp_name=claude_novelty_50gen \
    +evo_config.agentic_mode=true +evo_config.agentic.backend=claude \
    ++evo_config.max_novelty_attempts=5 \
    ++evo_config.code_embed_sim_threshold=0.95 \
    ++evo_config.embedding_model=text-embedding-3-small \
    > /tmp/claude_novelty.log 2>&1 &
echo "Claude Novelty PID: $!"

echo ""
echo "=== All 6 experiments launched! ==="
echo "Logs:"
echo "  /tmp/codex_basic.log"
echo "  /tmp/codex_novelty.log"
echo "  /tmp/gemini_basic.log"
echo "  /tmp/gemini_novelty.log"
echo "  /tmp/claude_basic.log"
echo "  /tmp/claude_novelty.log"
