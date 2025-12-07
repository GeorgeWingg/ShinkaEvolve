#!/bin/bash
# Launch 3 baseline runs for Circle Packing with different backends
# Target: ≥2.635983 sum of radii OR 30 iterations

set -e
cd /Users/juno/workspace/shrinkaevolve

# Source environment
source .env
export OPENAI_API_KEY

BACKEND=$1

case $BACKEND in
  codex)
    echo "Launching Codex backend..."
    uv run shinka_launch \
      variant=circle_packing_example \
      evolution=agentic \
      ++evo_config.agentic.backend=codex \
      '++evo_config.agentic.extra_cli_config.model=gpt-5.1-codex-mini' \
      '++evo_config.agentic.extra_cli_config.model_reasoning_effort=high' \
      ++evo_config.num_generations=30
    ;;
  claude)
    echo "Launching Claude backend..."
    uv run shinka_launch \
      variant=circle_packing_example \
      evolution=agentic \
      ++evo_config.agentic.backend=claude \
      '++evo_config.agentic.extra_cli_config.model=claude-sonnet-4-5-20250929' \
      ++evo_config.num_generations=30
    ;;
  gemini)
    echo "Launching Gemini backend..."
    # max_turns=200 to avoid GeminiExecutionError: Gemini emitted more events than allowed
    uv run shinka_launch \
      variant=circle_packing_example \
      evolution=agentic \
      ++evo_config.agentic.backend=gemini \
      '++evo_config.agentic.extra_cli_config.model=gemini-3-pro-preview' \
      ++evo_config.agentic.max_turns=200 \
      ++evo_config.num_generations=30
    ;;
  *)
    echo "Usage: $0 {codex|claude|gemini}"
    exit 1
    ;;
esac
