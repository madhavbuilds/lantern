"""
Lantern honest benchmark — measures real /api/ask latency, writes SPECS.md.

Requires a running Lantern server at --base (default http://127.0.0.1:3000)
unless --models is set, in which case a temporary uvicorn is spawned per model
with MODEL=<name>. Does not reimplement the pipeline; consumes /api/ask SSE.

  python bench.py
  python bench.py --models qwen3:4b,qwen3:8b
  python bench.py --runs 5 --out SPECS.md
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import socket
import statistics
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
QUERIES = [
    "What is the capital of France?",
    "Who wrote Hamlet?",
    "What year did World War II end?",
    "Explain the difference between TCP and UDP.",
    "Compare photosynthesis and cellular respiration in two sentences.",
    "What are the main steps in HTTPS certificate validation?",
    "Is a tomato a fruit or a vegetable?",
    "Can you drink seawater if you boil it first?",
]
WORDS_PER_TOKEN = 0.75
GPU_WARN = (
    "WARNING: not 100% GPU (PROCESSOR={proc}). Drop one model size in Modelfile, "
    "then: ollama create lantern-qwen -f Modelfile"
)


def _run(cmd: list[str], timeout: float = 8.0) -> str:
    try:
        return subprocess.check_output(
            cmd, text=True, stderr=subprocess.DEVNULL, timeout=timeout
        ).strip()
    except Exception:
        return ""


def detect_host() -> dict:
    """OS/CPU/RAM/GPU summary. Missing fields degrade to unknown/None."""
    system = platform.system()
    os_name = f"{system} {platform.release()}".strip() or "unknown"
    cpu, ram_gb, gpu, vram_gb = "unknown", None, "unknown", None
    if system == "Darwin":
        cpu = _run(["sysctl", "-n", "machdep.cpu.brand_string"]) or "unknown"
        try:
            ram_gb = round(int(_run(["sysctl", "-n", "hw.memsize"]) or 0) / 1e9, 1)
        except ValueError:
            pass
        sp = _run(["system_profiler", "SPDisplaysDataType", "-json"], 20.0)
        try:
            chip = (json.loads(sp).get("SPDisplaysDataType") or [{}])[0]
            gpu = chip.get("sppci_model") or chip.get("_name") or "unknown"
            m = re.search(r"([\d.]+)\s*GB", str(chip.get("spdisplays_vram") or ""), re.I)
            vram_gb = float(m.group(1)) if m else (ram_gb if "Apple" in gpu else None)
        except Exception:
            pass
    elif system == "Windows":
        ps = ["powershell", "-NoProfile", "-Command"]
        cpu = _run(ps + ["(Get-CimInstance Win32_Processor).Name"]) or "unknown"
        try:
            ram_gb = round(
                int(_run(ps + ["(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory"]) or 0)
                / 1e9,
                1,
            )
        except ValueError:
            pass
        g = _run(ps + ["(Get-CimInstance Win32_VideoController).Name"])
        gpu = (g.splitlines()[0].strip() if g else "unknown") or "unknown"
    else:
        cpu = (
            _run(["bash", "-c", "lscpu | awk -F: '/Model name/{print $2;exit}'"]).strip()
            or platform.processor()
            or "unknown"
        )
        try:
            ram_gb = round(
                int(_run(["bash", "-c", "awk '/MemTotal/{print $2}' /proc/meminfo"]) or 0) / 1e6, 1
            )
        except ValueError:
            pass
        gpu = _run(["bash", "-c", "nvidia-smi --query-gpu=name --format=csv,noheader"]) or "unknown"
        gpu = gpu.splitlines()[0].strip() if gpu != "unknown" else gpu
        vm = _run(
            ["bash", "-c", "nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits"]
        )
        try:
            vram_gb = round(float(vm.splitlines()[0]) / 1024, 1) if vm else None
        except ValueError:
            pass
    return {
        "os": os_name,
        "cpu": cpu,
        "ram_gb": ram_gb,
        "gpu": gpu,
        "vram_gb": vram_gb,
        "ollama_version": _run(["ollama", "--version"]) or "unknown",
    }


def ollama_processor(model: str | None = None) -> str:
    out = _run(["ollama", "ps"], 10.0)
    lines = [ln for ln in out.splitlines() if ln.strip()]
    for ln in lines[1:]:
        if model and model.split(":")[0] not in ln and model not in ln:
            continue
        m = re.search(r"(\d+%/+\d+%\s*CPU/GPU|\d+%\s*GPU|CPU)\b", ln)
        if m:
            return re.sub(r"\s+", " ", m.group(1))
    return "unknown"


def is_full_gpu(processor: str) -> bool:
    return bool(re.search(r"^100%\s*GPU$", (processor or "").strip(), re.I))


def gpu_warning_needed(processor: str) -> bool:
    p = (processor or "").strip()
    return bool(p) and p.lower() != "unknown" and not is_full_gpu(p)


def model_quant(model: str) -> str:
    m = re.search(r"quantization\s+(\S+)", _run(["ollama", "show", model], 15.0), re.I)
    return m.group(1) if m else "unknown"


def host_payload(model: str) -> dict:
    h = detect_host()
    proc = ollama_processor(model)
    return {
        **{k: h[k] for k in ("os", "cpu", "ram_gb", "gpu", "vram_gb", "ollama_version")},
        "model": model,
        "processor": proc,
        "quantization": model_quant(model),
        # None = not loaded / unknown — UI must not treat that as a CPU split.
        "full_gpu": True if is_full_gpu(proc) else (False if gpu_warning_needed(proc) else None),
    }


def _pct(xs: list[float], p: float) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    return s[max(0, min(len(s) - 1, int(round(p * (len(s) - 1)))))]


def ask_once(base: str, query: str, mode: str, timeout: float = 300.0) -> dict:
    url = f"{base.rstrip('/')}/api/ask?" + urllib.parse.urlencode(
        {"q": query, "mode": mode, "tools": "0"}
    )
    event, answer, timing, err = None, [], None, None
    with urllib.request.urlopen(
        urllib.request.Request(url, headers={"Accept": "text/event-stream"}), timeout=timeout
    ) as resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace").rstrip("\n")
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:") and event:
                data = line[5:].strip()
                if event == "token":
                    try:
                        answer.append(json.loads(data))
                    except json.JSONDecodeError:
                        answer.append(data)
                elif event == "timing":
                    timing = json.loads(data)
                elif event == "error":
                    try:
                        err = json.loads(data)
                    except json.JSONDecodeError:
                        err = data
                    break
    if err:
        raise RuntimeError(err)
    if not timing:
        raise RuntimeError("no timing event")
    text = "".join(answer)
    out_tok = int(len(text.split()) / WORDS_PER_TOKEN) if text else 0
    ttft = float(timing.get("ttft_ms") or 0)
    total = float(timing.get("total_ms") or 0)
    prefill = max(
        0.0,
        ttft
        - float(timing.get("search_ms") or 0)
        - float(timing.get("fetch_ms") or 0)
        - float(timing.get("select_ms") or 0),
    )
    gen_s = max(0.001, (total - ttft) / 1000.0)
    return {
        "ttft_ms": ttft,
        "prefill_ms": prefill,
        "total_ms": total,
        "context_tokens": int(timing.get("context_tokens") or 0),
        "gen_tok_s": (out_tok / gen_s) if out_tok else 0.0,
    }


def server_up(base: str) -> bool:
    try:
        urllib.request.urlopen(base.rstrip("/") + "/", timeout=3)
        return True
    except Exception:
        return False


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_temp_server(model: str) -> tuple[subprocess.Popen, str]:
    port = free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "server:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(ROOT),
        env={**os.environ, "MODEL": model},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    for _ in range(60):
        if proc.poll() is not None:
            raise RuntimeError(f"temp server for {model} exited early")
        if server_up(base):
            return proc, base
        time.sleep(0.4)
    proc.kill()
    raise RuntimeError(f"temp server for {model} did not become ready")


def aggregate(samples: list[dict]) -> dict:
    def col(k):
        return [float(s[k]) for s in samples if s.get(k) is not None]

    ttft, total, pref, ctx, gen = map(
        col, ("ttft_ms", "total_ms", "prefill_ms", "context_tokens", "gen_tok_s")
    )
    med = statistics.median
    return {
        "ttft_ms": med(ttft) if ttft else None,
        "ttft_p90_ms": _pct(ttft, 0.9),
        "prefill_ms": med(pref) if pref else None,
        "total_ms": med(total) if total else None,
        "context_tokens": int(med(ctx)) if ctx else 0,
        "gen_tok_s": med(gen) if gen else None,
        "n": len(samples),
    }


def cell(a: dict) -> str:
    if not a or a.get("ttft_ms") is None:
        return "—"
    return f"{a['ttft_ms'] / 1000:.1f}s first word · {a['gen_tok_s'] or 0:.0f} tok/s"


def write_specs(path: Path, host: dict, results: dict, warns: list[str]):
    lines = [
        "# Lantern measured performance",
        "",
        "## Measured on",
        "",
        f"- Date: {date.today().isoformat()}",
        f"- OS: {host.get('os', 'unknown')}",
        f"- CPU: {host.get('cpu', 'unknown')}",
        f"- RAM: {host.get('ram_gb') or 'unknown'} GB",
        f"- GPU: {host.get('gpu', 'unknown')}"
        + (f" ({host['vram_gb']} GB)" if host.get("vram_gb") else ""),
        f"- Ollama: {host.get('ollama_version', 'unknown')}",
        "",
    ]
    for model, modes in results.items():
        proc, quant = modes.get("_processor", "unknown"), modes.get("_quant", "unknown")
        tag = (
            "100% GPU"
            if is_full_gpu(str(proc))
            else ("**not 100% GPU**" if gpu_warning_needed(str(proc)) else "placement unknown")
        )
        lines.append(f"- Model `{model}` ({quant}): PROCESSOR `{proc}` — {tag}")
    lines += ["", "## Results", "", "| Model | Fast | Deep |", "| --- | --- | --- |"]
    for model, modes in results.items():
        lines.append(
            f"| `{model}` | {cell(modes.get('fast') or {})} | {cell(modes.get('deep') or {})} |"
        )
    lines += [
        "",
        "### Detail (medians)",
        "",
        "| Model | Mode | TTFT | p90 | Prefill | Total | ctx | tok/s | n |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for model, modes in results.items():
        for mode in ("fast", "deep"):
            a = modes.get(mode) or {}
            if not a or a.get("ttft_ms") is None:
                continue
            lines.append(
                f"| `{model}` | {mode} | {a['ttft_ms']:.0f} ms | {a['ttft_p90_ms']:.0f} ms | "
                f"{a['prefill_ms']:.0f} ms | {a['total_ms']:.0f} ms | {a['context_tokens']} | "
                f"{a['gen_tok_s']:.0f} | {a['n']} |"
            )

    lines += [
        "",
        "## Caveats",
        "",
        "- Medians from the fixed 8-query set in `bench.py` on this one machine.",
        "- Your numbers will differ with hardware, model load, and search latency.",
        "- Time-to-first-word is not time-to-full-answer; prefill is TTFT minus search/fetch/select.",
        "",
    ]
    if warns:
        lines += ["## Warnings", ""] + [f"- {w}" for w in warns] + [""]
    path.write_text("\n".join(lines), encoding="utf-8")


def bench_model(base: str, model: str, runs: int) -> tuple[dict, str]:
    print(f"\n=== {model} @ {base} ===\n  warm-up…", flush=True)
    try:
        ask_once(base, QUERIES[0], "fast")
    except Exception as e:
        print(f"  warm-up failed: {e}", flush=True)
    proc = ollama_processor(model)
    print(f"  PROCESSOR: {proc}", flush=True)
    if gpu_warning_needed(proc):
        print(GPU_WARN.format(proc=proc), flush=True)
    modes = {"_processor": proc, "_quant": model_quant(model)}
    for mode in ("fast", "deep"):
        samples = []
        for r in range(runs):
            for i, q in enumerate(QUERIES):
                print(f"  {mode} {r + 1}/{runs} q{i + 1}/{len(QUERIES)}…", flush=True)
                try:
                    samples.append(ask_once(base, q, mode))
                except Exception as e:
                    print(f"    skip: {e}", flush=True)
        modes[mode] = aggregate(samples)
        a = modes[mode]
        if a.get("ttft_ms") is not None:
            print(
                f"  {mode}: TTFT {a['ttft_ms']:.0f} ms (p90 {a['ttft_p90_ms']:.0f}) · "
                f"{a['gen_tok_s']:.0f} tok/s",
                flush=True,
            )
    return modes, proc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Lantern honest /api/ask benchmark")
    ap.add_argument("--base", default="http://127.0.0.1:3000")
    ap.add_argument("--models", default="")
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--out", default=str(ROOT / "SPECS.md"))
    args = ap.parse_args(argv)
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    results, warns, host = {}, [], detect_host()

    if not models:
        if not server_up(args.base):
            print(
                f"Server not reachable at {args.base}. Start it first:\n"
                "  uvicorn server:app --host 127.0.0.1 --port 3000\n"
                "Or pass --models name to spawn a temporary server.",
                file=sys.stderr,
            )
            return 1
        model = os.getenv("MODEL", "lantern-qwen")
        try:
            with urllib.request.urlopen(args.base.rstrip("/") + "/api/host", timeout=5) as r:
                model = json.load(r).get("model") or model
        except Exception:
            pass
        modes, proc = bench_model(args.base, model, args.runs)
        results[model] = modes
        if gpu_warning_needed(proc):
            warns.append(GPU_WARN.format(proc=proc))
    else:
        for model in models:
            child = None
            try:
                child, base = start_temp_server(model)
                modes, proc = bench_model(base, model, args.runs)
                results[model] = modes
                if gpu_warning_needed(proc):
                    warns.append(f"{model}: " + GPU_WARN.format(proc=proc))
            finally:
                if child and child.poll() is None:
                    child.terminate()
                    try:
                        child.wait(timeout=8)
                    except subprocess.TimeoutExpired:
                        child.kill()

    write_specs(Path(args.out), host, results, warns)
    print(f"\nWrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
