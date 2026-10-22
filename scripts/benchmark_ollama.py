"""Compare CPU and Vulkan on an isolated local Ollama server; no Telegram calls."""

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from us_market_monitor.processors.ollama_preview import (  # noqa: E402
    SCHEMA,
    SYSTEM,
    protect_numbers,
    restore_numbers,
    validate_result,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=11435)
    parser.add_argument("--rounds", type=int, default=2)
    args = parser.parse_args()
    session = requests.Session()
    session.trust_env = False
    base = f"http://127.0.0.1:{args.port}"
    source = json.loads((ROOT / "data/ollama-preview/event-94.json").read_text(encoding="utf-8"))["source_text"]
    protected, values = protect_numbers(source)
    messages = [
        {"role": "system", "content": SYSTEM + "\nThis is an earnings report. Lead with revenue growth and rising costs; then net income/EPS and guidance."},
        {"role": "user", "content": json.dumps({"source_text": protected}, ensure_ascii=False)},
    ]
    output = ROOT / "data/ollama-benchmark"
    output.mkdir(exist_ok=True)
    results = []

    def post(path, payload):
        response = session.post(base + path, json=payload, timeout=(10, 600))
        response.raise_for_status()
        return response.json()

    try:
        for label, layers in [("cpu", 0), ("vulkan", 99)]:
            options = {"temperature": 0, "seed": 42, "num_ctx": 8192, "num_predict": 600,
                       "num_gpu": layers}
            print(json.dumps({"backend": label, "state": "warmup"}), flush=True)
            post("/api/generate", {"model": "qwen3.5:9b", "prompt": "Say hello.",
                                   "think": False, "stream": False, "keep_alive": "10m",
                                   "options": {**options, "num_predict": 8}})
            devices = session.get(base + "/api/ps", timeout=10).json()["models"]
            if label == "vulkan" and not any(m.get("size_vram", 0) > 0 for m in devices):
                raise RuntimeError("GPU acceleration not active; CPU fallback is not a GPU benchmark")
            if label == "cpu" and any(m.get("size_vram", 0) > 0 for m in devices):
                raise RuntimeError("CPU baseline unexpectedly uses GPU")
            for run in range(1, args.rounds + 1):
                print(json.dumps({"backend": label, "run": run, "state": "running"}), flush=True)
                started = time.monotonic()
                data = post("/api/chat", {"model": "qwen3.5:9b", "messages": messages,
                                          "think": False, "stream": False, "format": SCHEMA,
                                          "keep_alive": "10m", "options": options})
                wall = time.monotonic() - started
                checked, error = False, None
                try:
                    restored = restore_numbers(json.loads(data["message"]["content"]), values)
                    validate_result(restored, source)
                    checked = True
                except (ValueError, KeyError, TypeError) as exc:
                    error = str(exc)
                row = {"backend": label, "run": run, "wall_seconds": round(wall, 3),
                       "prompt_tokens": data.get("prompt_eval_count"),
                       "prompt_seconds": data.get("prompt_eval_duration", 0) / 1e9,
                       "output_tokens": data.get("eval_count"),
                       "generation_seconds": data.get("eval_duration", 0) / 1e9,
                       "load_seconds": data.get("load_duration", 0) / 1e9,
                       "tokens_per_second": round(data.get("eval_count", 0) / max(data.get("eval_duration", 1) / 1e9, 1e-9), 3),
                       "done_reason": data.get("done_reason"), "validated": checked, "validation_error": error,
                       "devices": devices}
                results.append(row)
                (output / f"{label}-{run}.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                (output / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
                print(json.dumps(row), flush=True)
            post("/api/generate", {"model": "qwen3.5:9b", "keep_alive": 0})
        summary = {label: {"mean_wall_seconds": statistics.mean(r["wall_seconds"] for r in results if r["backend"] == label),
                           "mean_tokens_per_second": statistics.mean(r["tokens_per_second"] for r in results if r["backend"] == label)}
                   for label in ["cpu", "vulkan"]}
        (output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(json.dumps({"summary": summary}), flush=True)
    finally:
        post("/api/generate", {"model": "qwen3.5:9b", "keep_alive": 0})


if __name__ == "__main__":
    main()
