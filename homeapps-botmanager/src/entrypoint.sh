#!/bin/bash
# Container entrypoint.
# 1) Make sure the persistent data tree exists.
# 2) Seed /data/src from the packaged source the first time the app runs.
# 3) Install Python deps into /data/.local (survives image rebuilds).
# 4) Start the dashboard + bot supervisor.

set -euo pipefail

DATA_DIR="${FXBOT_DATA_DIR:-/data}"
PACKAGED="${FXBOT_PACKAGED_SRC:-/opt/default-src}"

mkdir -p "${DATA_DIR}/src" "${DATA_DIR}/logs" "${DATA_DIR}/config" "${DATA_DIR}/.local"

# First boot: copy the readable, commented source into the editable volume.
if [ ! -f "${DATA_DIR}/src/main.py" ]; then
  echo "[entrypoint] Seeding editable source into ${DATA_DIR}/src"
  cp -a "${PACKAGED}/." "${DATA_DIR}/src/"
fi

# Always refresh the entrypoint copy itself is unnecessary; users edit /data/src.
export HOME="${DATA_DIR}"
export PATH="${DATA_DIR}/.local/bin:${PATH}"
export PIP_CACHE_DIR="${DATA_DIR}/.cache/pip"
mkdir -p "${PIP_CACHE_DIR}"

echo "[entrypoint] Installing Python dependencies"
python -m pip install --user -q -r "${DATA_DIR}/src/requirements.txt"

echo "[entrypoint] Starting FX Bot"
cd "${DATA_DIR}/src"
exec python main.py
