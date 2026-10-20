#!/bin/bash
# build.sh [config.env] — raw/ -> corpus/ (deduplicated, scrubbed) -> graphify graph with config edges.
set -eu
cd "$(dirname "$0")"; CFG=${1:-config.env}; . "./$CFG"
C=$OUT/corpus; mkdir -p "$C"
python3 -I layout.py "$OUT/raw" "$C" $GIT_DIRS
python3 -I clean_secrets.py "$C"
graphify update "$C" --force >/dev/null           # AST pass over code, no LLM
python3 -I cfg_graph.py "$C" "$HOSTS_FILE" "$C/graphify-out/graph.json"
graphify cluster-only "$C" --no-label --no-viz    # re-cluster with config edges, rewrite GRAPH_REPORT.md
echo "graph: $C/graphify-out/graph.json   report: $C/graphify-out/GRAPH_REPORT.md"
