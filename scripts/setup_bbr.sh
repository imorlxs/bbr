#!/usr/bin/env bash
set -euo pipefail

compose_cmd=(docker compose)
containers=(sender sender2)

if [[ $# -gt 0 ]]; then
  containers=("$@")
fi

echo "[1/3] Loading tcp_bbr on host"
if command -v sudo >/dev/null 2>&1; then
  if ! sudo modprobe tcp_bbr; then
    echo "WARNING: sudo modprobe failed; retrying without sudo"
    modprobe tcp_bbr
  fi
else
  echo "WARNING: sudo not found; attempting modprobe directly"
  modprobe tcp_bbr
fi

echo "[2/3] Verifying host supports BBR"
if ! sysctl net.ipv4.tcp_available_congestion_control | grep -qw bbr; then
  echo "ERROR: bbr not available in host kernel" >&2
  exit 1
fi

echo "[3/3] Enabling fq + bbr in containers: ${containers[*]}"
for c in "${containers[@]}"; do
  "${compose_cmd[@]}" exec -T "$c" bash -lc '
    if [[ -w /proc/sys/net/core/default_qdisc ]]; then
      sysctl -w net.core.default_qdisc=fq >/dev/null
    fi
    if sysctl -w net.ipv4.tcp_congestion_control=bbr >/dev/null 2>&1; then
      sysctl net.ipv4.tcp_congestion_control
    else
      echo "WARNING: could not set net.ipv4.tcp_congestion_control inside container"
      sysctl net.ipv4.tcp_available_congestion_control || true
    fi
  '
done

echo "BBR setup complete."
