# Specialized agents

These charters separate responsibilities without hiding integration decisions. Every agent follows the repository-level `AGENTS.md`.

- `systems-architect`: interfaces, ADRs, integration boundaries
- `hardware-safety`: inventory, wiring, calibration, guarded bring-up
- `perception-ml`: face mesh, datasets, training, evaluation
- `emotion-ml`: affect representation, uncertainty, responsible evaluation
- `motion-control`: expression-to-motion mapping and controller adapters
- `mlops-evaluation`: reproducibility, experiment tracking, model packaging
- `research-provenance`: literature, prior repositories, licensing, provenance
- `qa-simulation`: tests, replay fixtures, simulation, failure injection

