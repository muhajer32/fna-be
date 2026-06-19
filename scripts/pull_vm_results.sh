#!/usr/bin/env bash
# pull_vm_results.sh - sync isolated run folders back from a cloud VM.
#
# Each model run on the VM lands in its own  data/outputs/runs/<run_id>/  folder
# (CSVs, charts, logs, the output workbook and run_metadata.json). This script
# rsyncs those run folders to your local machine *preserving the run_id*, so
# nothing from different runs/scenarios/settings gets mixed together.
#
# Usage:
#   scripts/pull_vm_results.sh root@YOUR_IP                # pull ALL runs
#   scripts/pull_vm_results.sh root@YOUR_IP <run_id>       # pull one run
#   scripts/pull_vm_results.sh root@YOUR_IP newest         # pull the newest run folder
#
# Env overrides:
#   REMOTE_ROOT   remote project root (default: /root/fna)
#   LOCAL_ROOT    local project root  (default: this repo)
set -euo pipefail

HOST="${1:?Usage: pull_vm_results.sh user@host [run_id|newest]}"
WHICH="${2:-all}"
REMOTE_ROOT="${REMOTE_ROOT:-/root/fna}"
LOCAL_ROOT="${LOCAL_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

REMOTE_RUNS="${REMOTE_ROOT}/data/outputs/runs"
LOCAL_RUNS="${LOCAL_ROOT}/data/outputs/runs"
mkdir -p "${LOCAL_RUNS}"

sync_registry() {
  mkdir -p "${LOCAL_RUNS}/index"
  rsync -avz "${HOST}:${REMOTE_RUNS}/index/runs_index.csv" "${LOCAL_RUNS}/index/runs_index.csv" 2>/dev/null || true
}

echo "Source : ${HOST}:${REMOTE_RUNS}"
echo "Target : ${LOCAL_RUNS}"

case "${WHICH}" in
  all)
    echo "Pulling ALL run folders + registry..."
    rsync -avz --partial --progress --exclude latest --exclude latest.txt "${HOST}:${REMOTE_RUNS}/" "${LOCAL_RUNS}/"
    ;;
  newest)
    RID=$(ssh "${HOST}" "find '${REMOTE_RUNS}' -mindepth 1 -maxdepth 1 -type d ! -name index -print | sort | tail -n 1 | xargs -r basename")
    if [[ -z "${RID}" ]]; then echo "Could not resolve newest run on ${HOST}." >&2; exit 1; fi
    echo "Newest run on VM: ${RID}"
    rsync -avz --partial --progress "${HOST}:${REMOTE_RUNS}/${RID}/" "${LOCAL_RUNS}/${RID}/"
    sync_registry
    ;;
  *)
    echo "Pulling run: ${WHICH}"
    rsync -avz --partial --progress "${HOST}:${REMOTE_RUNS}/${WHICH}/" "${LOCAL_RUNS}/${WHICH}/"
    sync_registry
    ;;
esac

echo
echo "Done. Local runs:"
find "${LOCAL_RUNS}" -mindepth 1 -maxdepth 1 -type d ! -name index -exec basename {} \; | sort || true
echo
echo "Cross-run registry: ${LOCAL_RUNS}/index/runs_index.csv"
