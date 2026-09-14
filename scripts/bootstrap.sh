#!/usr/bin/env bash

set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
venv_path="${project_root}/.venv"
export PIP_CACHE_DIR="${project_root}/.cache/pip"
export PIP_DISABLE_PIP_VERSION_CHECK=1

python3 -m venv "${venv_path}"
"${venv_path}/bin/python" -m pip install --requirement "${project_root}/requirements-dev.lock"

echo "Environment ready. Activate it with: source .venv/bin/activate"
