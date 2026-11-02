#!/usr/bin/env bash
set -euo pipefail

run_command_with_retries() {
  local cmd="$1"
  local max_retries="${2:-2}"
  local attempt=1
  local max_attempts=$((max_retries + 1))
  local rc=0

  while [ "$attempt" -le "$max_attempts" ]; do
    echo "Attempt $attempt/$max_attempts: $cmd"
    bash -c "$cmd"
    rc=$?

    if [ "$rc" -eq 0 ]; then
      echo "Command succeeded on attempt $attempt."
      return 0
    fi

    if [ "$attempt" -lt "$max_attempts" ]; then
      echo "Command failed with exit code $rc. Retrying..."
      sleep 2
    fi

    attempt=$((attempt + 1))
  done

  echo "Command failed after $max_attempts attempts. Final exit code: $rc"
  return "$rc"
}

pip config set global.trusted-host "fcp4lnexuap01.bocad.bank-banque-canada.ca"
pip config set global.index "https://fcp4lnexuap01.bocad.bank-banque-canada.ca/nexus/repository/pypi-all/"
pip config set global.index_url "https://fcp4lnexuap01.bocad.bank-banque-canada.ca/nexus/repository/pypi-all/simple"

run_command_with_retries 'pip install "oeds-common~=1.0"' 3
run_command_with_retries "pip install -U certifi" 3