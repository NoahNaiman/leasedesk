#!/bin/bash
# Double-click to open LeaseDesk on the sandbox: fictional properties and tenants for
# trying things out. Your real data is not touched. Each start rebuilds a fresh sandbox.
cd "$(dirname "$0")" || exit 1

if [ ! -x .venv/bin/streamlit ]; then
  echo "Run \"Start LeaseDesk.command\" once first to finish setting up."
  read -r -p "Press Enter to close."
  exit 1
fi

echo "Building a fresh sandbox..."
.venv/bin/python scripts/build_sandbox.py || { read -r -p "Press Enter to close."; exit 1; }
echo "Starting LeaseDesk in sandbox mode. To stop it, close this window."
export LEASEDESK_DATA_DIR="$PWD/sandbox/data"
unset ANTHROPIC_API_KEY  # the sandbox never spends money
exec .venv/bin/streamlit run app/main.py --server.headless false --server.port 8502
