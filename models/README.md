# Models

Store model definitions, experiment configs, evaluation specifications, and model cards here. Large weights and datasets are external artifacts referenced by immutable version/checksum, not committed to Git.

Install model training and inference dependencies with `alice[ml]`; the base
package intentionally excludes PyTorch so control and capture deployments do
not download the ML runtime.
