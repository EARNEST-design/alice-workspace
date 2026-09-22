# Uses the Apache-2.0 Kev API: https://github.com/jaredpalmer/kev
# Pinned source: 90990a5fac2995b9faa3190f7d437e84f2067768
"""Private Kev CPU loader: bf16 load/merge, one-linear-at-a-time int8 conversion."""

import argparse
import ctypes
import gc
import time
from pathlib import Path

import torch
from torch.ao.quantization import quantize_dynamic


def quantize_linears(module):
    count = 0
    for name in list(module._modules):
        child = module._modules[name]
        if isinstance(child, torch.nn.Linear):
            child.float()
            packed = quantize_dynamic(
                torch.nn.Sequential(child),
                {torch.nn.Linear},
                dtype=torch.qint8,
                inplace=True,
            )[0]
            setattr(module, name, packed)
            count += 1
            del child, packed
            release_unused()
        else:
            count += quantize_linears(child)
    return count


def materialize_non_linears(module):
    for child in module.modules():
        if isinstance(child, torch.nn.Linear):
            continue
        for parameter in child.parameters(recurse=False):
            parameter.data = parameter.detach().to(dtype=torch.float32, copy=True)
        for name, value in child.named_buffers(recurse=False):
            setattr(child, name, value.clone())


def release_unused():
    gc.collect()
    ctypes.CDLL(None).malloc_trim(0)


def memory():
    return "; ".join(
        line
        for line in Path("/proc/self/status").read_text().splitlines()
        if line.startswith(("VmRSS:", "VmHWM:"))
    )


def main():
    import uvicorn
    from kev.checkpoint import Checkpoint, LoadOptions
    from kev.serve import Server, app

    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True)
    parser.add_argument("--port", type=int, default=8009)
    args = parser.parse_args()
    torch.set_num_threads(1)
    torch.set_num_interop_threads(1)
    torch.backends.quantized.engine = "fbgemm"
    torch.manual_seed(7)
    started = time.monotonic()
    checkpoint = Checkpoint(args.run)
    if checkpoint.meta.base != "Qwen/Qwen3-4B-Base":
        raise ValueError("private loader is qualified only for Qwen3-4B-Base")
    tokenizer, model = checkpoint.load(
        "cpu", LoadOptions(dtype=torch.bfloat16, merge=False)
    )
    print("loaded bf16", round(time.monotonic() - started, 2), memory(), flush=True)
    model.lm = model.lm.merge_and_unload()
    release_unused()
    print("merged bf16", round(time.monotonic() - started, 2), memory(), flush=True)
    materialize_non_linears(model.lm)
    release_unused()
    print(
        "materialized nonlinear storage",
        round(time.monotonic() - started, 2),
        memory(),
        flush=True,
    )
    count = quantize_linears(model.lm)
    model.float().eval()
    release_unused()
    torch.set_num_threads(6)
    print(
        "quantized",
        count,
        "linears; fp32 embeddings/norms/head",
        round(time.monotonic() - started, 2),
        memory(),
        flush=True,
    )
    app.state.server = Server(checkpoint, tokenizer, model, "cpu")
    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
