# Host profile: alice-brain

Inventory captured 2026-08-28. Do not commit machine IDs, boot IDs, MAC addresses, LAN addresses, credentials, or Tailscale node identity.

## Hardware

- Vendor/model: GMKtec NucBox M6 (`M6-001`)
- Firmware: version 103, dated 2024-04-11
- CPU: AMD Ryzen 5 6600H, 6 cores / 12 threads, AMD-V
- GPU: integrated AMD Radeon; no discrete NVIDIA GPU or NVIDIA tooling detected
- RAM: 27 GiB usable
- Storage: Lexar NM6A1 1 TB NVMe, ext4 root, EFI system partition
- Swap: 8 GiB

## Operating system

- Hostname: `alice-brain`
- Ubuntu 26.04.1 LTS (`resolute`), x86-64
- Kernel: `7.0.0-30-generic`
- Network interfaces: two Ethernet, Wi-Fi, Docker bridge, and Tailscale
- Tailscale: 1.102.3

## Container runtime

- Docker CE/client: 29.7.2
- containerd.io: 2.3.3
- Buildx plugin: 0.36.1
- Docker Compose plugin: 5.5.0
- Docker service: enabled and active

The bootstrap added user `alice` to the `docker` group and a fresh user process successfully reached Docker Engine 29.7.2. Existing shells still require logout/login to refresh supplementary groups. Docker-group membership is effectively root-equivalent and is intentional only because this is a dedicated robotics host.

## Privilege discrepancy

Passwordless sudo was corrected on 2026-08-28 and verified with `sudo -n true` from the host context. Codex sandbox processes may still carry `no_new_privs`; host-level operations require the product's explicit escalation boundary even when host sudoers allows them.

## Connected robotics hardware

See `docs/discovery/hardware-inventory.md`. The project udev rule is installed and the Pololu native USB node is now mode `0666`; serial ports remain `root:dialout`. A read-only `UscCmd --getconf` export succeeded. No actuator commands were issued.
