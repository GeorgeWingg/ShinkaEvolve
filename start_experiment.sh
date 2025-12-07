#!/bin/bash
nohup uv run shinka_launch variant=circle_packing_example > /Users/juno/workspace/shrinkaevolve/launch.log 2>&1 &
PID_LAUNCH=$!
echo "Launched evolution with PID $PID_LAUNCH"

nohup uv run shinka_visualize results --port 8000 > /Users/juno/workspace/shrinkaevolve/viz.log 2>&1 &
PID_VIZ=$!
echo "Launched visualization with PID $PID_VIZ"

echo $PID_LAUNCH > launch.pid
echo $PID_VIZ > viz.pid
