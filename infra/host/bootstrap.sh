#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run from a working administrative session: sudo $0" >&2
  exit 1
fi

if [[ ! -f /etc/os-release ]]; then
  echo "Unsupported host: /etc/os-release is missing" >&2
  exit 1
fi

# shellcheck disable=SC1091
source /etc/os-release
if [[ ${ID:-} != ubuntu || ${VERSION_CODENAME:-} != resolute ]]; then
  echo "Expected Ubuntu resolute; found ${ID:-unknown} ${VERSION_CODENAME:-unknown}" >&2
  exit 1
fi

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y \
  ca-certificates \
  curl \
  git \
  libusb-1.0-0-dev \
  mono-runtime

install -o root -g root -m 0644 \
  "$(dirname "$0")/udev/99-pololu.rules" \
  /etc/udev/rules.d/99-pololu.rules
udevadm control --reload-rules
udevadm trigger --subsystem-match=usb --attr-match=idVendor=1ffb

if getent group docker >/dev/null 2>&1; then
  usermod -aG docker alice
else
  echo "Docker group is missing; install Docker Engine before continuing." >&2
  exit 1
fi

systemctl enable --now docker

echo "Host prerequisites applied. Log alice out and in to activate docker membership."
