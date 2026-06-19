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
#   scripts/pull_vm_results.sh root@YOUR_IP latest         # pull only the latest run
#
# Env overrides:
#   REMOTE_ROOT   remote project root (default: /root/fna)
#   LOCAL_ROOT    local project root  (default: this repo)
set -euo pipefail

HOST="${1:?Usage: pull_vm_results.sh user@host [run_id|latest]}"
WHICH="${2:-all}"
REMOTE_ROOT="${REMOTE_ROOT:-/root/fna}"
LOCAL_ROOT="${LOCAL_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

REMOTE_RUNS="${REMOTE_ROOT}/data/outputs/runs"
LOCAL_RUNS="${LOCAL_ROOT}/data/outputs/runs"
mkdir -p "${LOCAL_RUNS}"

echo "Source : ${HOST}:${REMOTE_RUNS}"
echo "Target : ${LOCAL_RUNS}"

case "${WHICH}" in
  all)
    echo "Pulling ALL run folders + registry..."
    rsync -avz --partial --progress "${HOST}:${REMOTE_RUNS}/" "${LOCAL_RUNS}/"
    ;;
  latest)
    # Resolve the run_id the VM's 'latest' pointer references, then pull it.
    RID=$(ssh "${HOST}" "readlink -f '${REMOTE_RUNS}/latest' 2>/dev/null | xargs -r basename || cat '${REMOTE_RUNS}/latest.txt' 2>/dev/null | xargs -r basename")
    if [[ -z "${RID}" ]]; then echo "Could not resolve latest run on ${HOST}." >&2; exit 1; fi
    echo "Latest run on VM: ${RID}"
    rsync -avz --partial --progress "${HOST}:${REMOTE_RUNS}/${RID}/" "${LOCAL_RUNS}/${RID}/"
    rsync -avz "${HOST}:${REMOTE_RUNS}/runs_index.csv" "${LOCAL_RUNS}/runs_index.csv" 2>/dev/null || true
    ;;
  *)
    echo "Pulling run: ${WHICH}"
    rsync -avz --partial --progress "${HOST}:${REMOTE_RUNS}/${WHICH}/" "${LOCAL_RUNS}/${WHICH}/"
    rsync -avz "${HOST}:${REMOTE_RUNS}/runs_index.csv" "${LOCAL_RUNS}/runs_index.csv" 2>/dev/null || true
    ;;
esac

echo
echo "Done. Local runs:"
ls -1 "${LOCAL_RUNS}" | grep -v '^runs_index.csv$' | grep -v '^latest' || true
echo
echo "Cross-run registry: ${LOCAL_RUNS}/runs_index.csv"
