#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Patrick Leiverkus
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# Provision an Ubuntu 24.04 host to build and run the CUDA image: Docker CE +
# NVIDIA Container Toolkit. Idempotent — safe to re-run.
#
# Does NOT install the NVIDIA driver: that needs a reboot, so it stays a manual
# step (the script tells you the command and stops). Does NOT open any port.
#
# Usage: sudo ./scripts/provision-gpu-host.sh
#
set -euo pipefail

say()  { echo "[provision] $*"; }
die()  { echo "[provision] ERROR: $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "run with sudo"

# --- 0. Host sanity -------------------------------------------------------
# shellcheck source=/dev/null
. /etc/os-release
say "host: ${PRETTY_NAME:-unknown} ($(uname -m))"
[[ "${ID:-}" == "ubuntu" ]] || say "WARN: not Ubuntu — apt repo lines assume Debian-family"
[[ "$(uname -m)" == "x86_64" ]] || die "the CUDA image is x86_64 only (nvidia/cuda base has no arm64)"

# --- 1. NVIDIA driver (prerequisite, not installed here) ------------------
# The toolkit install docs require the driver to be present first.
if ! command -v nvidia-smi >/dev/null 2>&1; then
  cat >&2 <<'EOF'
[provision] ERROR: no nvidia-smi — the GPU driver is not installed.

Install it, reboot, then re-run this script:

    sudo ubuntu-drivers install
    sudo reboot

EOF
  exit 1
fi

DRIVER="$(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -1)"
GPU_NAME="$(nvidia-smi --query-gpu=name --format=csv,noheader | head -1)"
COMPUTE_CAP="$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader | head -1)"
say "GPU: ${GPU_NAME} (driver ${DRIVER}, compute capability ${COMPUTE_CAP})"

# CUDA 12.x containers need a >= 525 driver (CUDA minor-version compatibility).
if [[ "${DRIVER%%.*}" -lt 525 ]]; then
  die "driver ${DRIVER} is too old for the CUDA 12.8 base image (need >= 525)"
fi

# --- 2. Docker CE ---------------------------------------------------------
if command -v docker >/dev/null 2>&1; then
  say "docker already installed: $(docker --version)"
else
  say "installing Docker CE from download.docker.com"
  apt-get update -qq
  apt-get install -y -qq ca-certificates curl gnupg
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -qq
  apt-get install -y -qq docker-ce docker-ce-cli containerd.io \
                         docker-buildx-plugin docker-compose-plugin
  say "installed: $(docker --version)"
fi

# --- 3. NVIDIA Container Toolkit -----------------------------------------
if command -v nvidia-ctk >/dev/null 2>&1; then
  say "nvidia-container-toolkit already installed: $(nvidia-ctk --version | head -1)"
else
  say "installing nvidia-container-toolkit from nvidia.github.io"
  curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
    | gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
  curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
    | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
    > /etc/apt/sources.list.d/nvidia-container-toolkit.list
  apt-get update -qq
  apt-get install -y -qq nvidia-container-toolkit
  say "installed: $(nvidia-ctk --version | head -1)"
fi

# --- 4. Wire the runtime into Docker -------------------------------------
say "configuring the docker runtime (nvidia-ctk runtime configure)"
nvidia-ctk runtime configure --runtime=docker >/dev/null
systemctl restart docker
docker info 2>/dev/null | grep -qi nvidia \
  || die "docker does not list the nvidia runtime after configure"
say "docker reports the nvidia runtime"

# --- 5. Device passthrough smoke test ------------------------------------
# The narrowest possible check: can a container see the GPU at all? Everything
# in verify-gpu-image.sh depends on this working.
say "testing device passthrough with the CUDA base image"
docker run --rm --gpus all "nvidia/cuda:12.8.1-base-ubuntu24.04" nvidia-smi -L \
  || die "container cannot see the GPU — passthrough is broken, stop here"

# --- 6. Docker group for the invoking user -------------------------------
TARGET_USER="${SUDO_USER:-}"
if [[ -n "$TARGET_USER" ]] && ! id -nG "$TARGET_USER" | grep -qw docker; then
  usermod -aG docker "$TARGET_USER"
  say "added '$TARGET_USER' to the docker group — log out and back in for it to apply"
fi

cat <<EOF

[provision] done. GPU: ${GPU_NAME}, compute capability ${COMPUTE_CAP}.

Next: build the CUDA image, narrowed to this card's architecture (much faster
than the default CUDA_ARCH=all-major):

    docker build --build-arg CUDA_ARCH="${COMPUTE_CAP/./}" -t effigies:gpu .

Then assert the image is genuinely CUDA-enabled:

    ./scripts/verify-gpu-image.sh effigies:gpu
EOF
