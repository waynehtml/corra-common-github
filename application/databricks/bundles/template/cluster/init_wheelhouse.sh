#!/bin/bash
set -euo pipefail

# Get the directory where this init script is located.
# requirements.txt and env.sh are expected to live beside this script.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REQ_FILE="${SCRIPT_DIR}/requirements.txt"
ENV_FILE="${SCRIPT_DIR}/env.sh"

# Use the Databricks cluster Python pip explicitly.
PIP_BIN="/databricks/python/bin/pip"

# Corporate Nexus PyPI proxy.
NEXUS_URL="https://fcp4lnexuap01.bocad.bank-banque-canada.ca/nexus/repository/pypi-all/simple"
NEXUS_HOST="fcp4lnexuap01.bocad.bank-banque-canada.ca"

# Retry a command a small number of times.
# This helps with transient install or network issues.
retry() {
  local max_retries=2
  local attempt=1

  while [ "${attempt}" -le "${max_retries}" ]; do
    if "$@"; then
      return 0
    fi
    attempt=$((attempt + 1))
    sleep 3
  done

  return 1
}

# requirements.txt must exist or there is nothing to install.
if [ ! -f "${REQ_FILE}" ]; then
  echo "requirements.txt not found: ${REQ_FILE}"
  exit 1
fi

# env.sh must exist and export CLUSTER_WHEELHOUSE_PATH.
if [ ! -f "${ENV_FILE}" ]; then
  echo "env.sh not found: ${ENV_FILE}"
  exit 1
fi

# Load environment variables from env.sh.
# shellcheck disable=SC1090
source "${ENV_FILE}"

# Trust the internal Nexus host.
"${PIP_BIN}" config set global.trusted-host "${NEXUS_HOST}"

echo "Trying wheelhouse: ${CLUSTER_WHEELHOUSE_PATH}"

# Install from the configured wheelhouse only.
if "${PIP_BIN}" install --no-index --find-links "${CLUSTER_WHEELHOUSE_PATH}" -r "${REQ_FILE}"; then
  echo "Installed from wheelhouse: ${CLUSTER_WHEELHOUSE_PATH}"
  exit 0
else
  echo "Wheelhouse install failed: ${CLUSTER_WHEELHOUSE_PATH}"
fi

# Fall back to Nexus if wheelhouse install fails.
echo "Falling back to Nexus"
if retry "${PIP_BIN}" install \
  --index-url "${NEXUS_URL}" \
  --retries 2 \
  --timeout 60 \
  -r "${REQ_FILE}"; then
  echo "Installed from Nexus"
  exit 0
fi

echo "Package installation failed"
exit 1