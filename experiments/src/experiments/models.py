"""Model loading and GPU diagnostics."""

from __future__ import annotations

import ctypes
import time

import spacy
import torch
from transformers import AutoTokenizer, pipeline as hf_pipeline

MODEL = "KennethTM/bert-base-uncased-danish"
SPACY_MODEL = "da_core_news_sm"
TOP_K = 50


def _gpu_device() -> int:
    """Return device id (0+) or -1 for CPU."""
    print(f"PyTorch version: {torch.__version__}")
    cuda_ver = torch.version.cuda
    print(f"CUDA built with: cu{''.join(cuda_ver.split('.')) if cuda_ver else 'N/A'}")
    print(f"CUDA available:  {torch.cuda.is_available()}")

    if not torch.cuda.is_available():
        try:
            libcuda = ctypes.CDLL("libcuda.so")
            rc = libcuda.cuInit(0)
            errors = {
                0: "SUCCESS",
                100: "NO_DEVICE",
                999: "UNKNOWN (driver bad state — reboot or run: sudo nvidia-smi -r)",
            }
            print(f"cuInit status:   {errors.get(rc, rc)}")
        except OSError:
            print("cuInit status:   libcuda.so not found")
        print("WARNING: Falling back to CPU")
        return -1

    print(f"CUDA device:     {torch.cuda.get_device_name(0)}")
    print(
        f"VRAM:            "
        f"{torch.cuda.get_device_properties(0).total_mem / 1024**3:.1f} GiB"
    )
    return 0


def load_models(
    model: str = MODEL,
    spacy_model: str = SPACY_MODEL,
    top_k: int = TOP_K,
):
    """Load all models and return (fill_mask, tokenizer, nlp, device).

    Also prints GPU diagnostics and timing.
    """
    device = _gpu_device()

    t0 = time.perf_counter()
    fill_mask = hf_pipeline("fill-mask", model=model, device=device, top_k=top_k)
    tokenizer = AutoTokenizer.from_pretrained(model)
    nlp = spacy.load(spacy_model)
    elapsed = time.perf_counter() - t0
    print(f"\nModel load time (cold start): {elapsed:.3f}s")

    if device >= 0:
        alloc = torch.cuda.memory_allocated(device) / 1024**2
        print(f"GPU memory after load:        {alloc:.0f} MiB")

    # warm-up
    fill_mask(f"Der var engang en {tokenizer.mask_token}")
    print("Warm-up: done\n")

    return fill_mask, tokenizer, nlp, device
