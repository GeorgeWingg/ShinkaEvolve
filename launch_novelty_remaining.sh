#!/bin/bash
cd /Users/juno/workspace/shrinkaevolve
source .env

# Gemini Novelty
uv run shinka_launch task=circle_packing variant=circle_packing_example evolution=large_budget evo_config.num_generations=50 exp_name=gemini_novelty_50gen +evo_config.agentic_mode=true +evo_config.agentic.backend=gemini +evo_config.agentic.extra_cli_config.model=gemini-3-pro-preview ++evo_config.max_novelty_attempts=5 ++evo_config.code_embed_sim_threshold=0.95 ++evo_config.embedding_model=text-embedding-3-small > /tmp/gemini_novelty.log 2>&1 &
echo "Gemini Novelty: $!"

# Claude Novelty  
uv run shinka_launch task=circle_packing variant=circle_packing_example evolution=large_budget evo_config.num_generations=50 exp_name=claude_novelty_50gen +evo_config.agentic_mode=true +evo_config.agentic.backend=claude ++evo_config.max_novelty_attempts=5 ++evo_config.code_embed_sim_threshold=0.95 ++evo_config.embedding_model=text-embedding-3-small > /tmp/claude_novelty.log 2>&1 &
echo "Claude Novelty: $!"
