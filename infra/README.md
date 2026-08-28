# Alice infrastructure

The host is managed infrastructure, while application and ML dependencies should run in containers whenever hardware access, latency, or GUI requirements do not make that impractical.

- `host/`: reproducible host bootstrap, udev rules, and machine profile
- future `compose/`: local service topology once component boundaries are selected

Host packages should be limited to the kernel/device stack, Docker, administration and observability essentials, and hardware tools that need native USB access. Pin container images by digest for stable deployments and keep development overrides separate.

