#!/bin/bash
set -euo pipefail

run_command_with_retries() {
  local cmd="$1"
  local max_retries="${2:-3}"
  local attempt=1
  local return_value=0

  while [ "$attempt" -le "$max_retries" ]; do
    echo "=================================================="
    echo "Attempt $attempt/$max_retries"
    echo "Running: $cmd"
    echo "=================================================="

    set +e
    eval "$cmd"
    return_value=$?
    set -e

    if [ "$return_value" -eq 0 ]; then
      echo "Success on attempt $attempt"
      return 0
    fi

    echo "Command failed with exit code $return_value"

    if [ "$attempt" -lt "$max_retries" ]; then
      echo "Retrying..."
      sleep 2
    fi

    attempt=$((attempt + 1))
  done

  echo "Command failed after $max_retries attempts: $cmd"
  return 1
}

echo "=== Configure pip to use Nexus ==="
run_command_with_retries 'pip config set global.trusted-host "fcp4lnexuap01.bocad.bank-banque-canada.ca"' 3
run_command_with_retries 'pip config set global.index-url "https://fcp4lnexuap01.bocad.bank-banque-canada.ca/nexus/repository/pypi-all/simple"' 3

echo "=== Install core dependencies ==="
run_command_with_retries "pip install azure-core" 3
run_command_with_retries "pip install azure-identity" 3
run_command_with_retries "pip install pendulum" 3
run_command_with_retries "pip install pandas" 3
run_command_with_retries 'pip install "jinja2>=3.1.0"' 3
run_command_with_retries 'pip install "jmespath>=1.0.1"' 3
run_command_with_retries "pip install paramiko" 3
run_command_with_retries "pip install python-qpid-proton" 3
run_command_with_retries "pip install smbprotocol" 3
run_command_with_retries "pip install requests" 3
run_command_with_retries "pip install python-gnupg" 3
run_command_with_retries "pip install pgpy" 3
run_command_with_retries "pip install py7zr" 3
run_command_with_retries "pip install pyzipper" 3
run_command_with_retries "pip install jsonschema" 3

echo "=== Install oeds-common ==="
run_command_with_retries 'pip install "oeds-common~=1.0"' 3

echo "=== Update certificates ==="
run_command_with_retries "pip install -U certifi" 3

echo "=== Done ==="
