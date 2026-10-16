# Voice performance and acceptance

Recorded on **2026-09-23** for the local Ollama, managed GPT-SoVITS, and Faster-Whisper pipeline. These are historical measurements on one machine, not current regression counts or a latency guarantee.

## Resource policy

Ollama and GPT-SoVITS can overlap on the target GPU; Faster-Whisper runs afterward on CPU under Backend exclusion.

- `TRANSCRIPTION_DEVICE=cpu` reserves GPU capacity for generation and speech. Select `auto` or `cuda` only after measuring the full pipeline.
- Keep one managed GPT-SoVITS worker for the Backend lifetime to avoid repeated cold starts and process races.
- Closing Elysia's HTTP client does not unload shared Ollama models, which other local clients may use.
- Cancellation releases logical ownership immediately. Native work may retain physical capacity until its bounded call returns.

Managed speech reports only `starting`, `ready`, or `unavailable`; unavailable reasons are `setup_unavailable` or `runtime_failed`. Paths and worker diagnostics remain private. **Read aloud** checks readiness before reserving a turn; Voice Call keeps text replies available when speech is unavailable.

Voice Profile and emotion form a restart-bound pair. Saving a new pair validates the exact catalog entry without starting a worker. A mismatch fails before the settings revision changes.

## Reproduce the benchmark

Prepare Windows with `nvidia-smi.exe`, the local Ollama model, a complete Faster-Whisper model, and a reviewed Voice Profile with matching GPT-SoVITS Runtime, weights, and reference audio. Local evaluation requires explicit `GPT_SOVITS_ALLOW_LOCAL_EVALUATION=True`.

Run from the repository root in Command Prompt:

```bat
.venv\Scripts\python.exe scripts\benchmark_voice_pipeline.py --cycles 3 --sample-interval-ms 250 --request-timeout-seconds 180
```

The benchmark accepts bounded numeric controls, enforces deadlines and response limits, samples global GPU state, overlaps generation and TTS, then transcribes the in-memory WAV on CPU. Fixed prompts, audio, and transcripts are never included in JSON output. Owned monitors, HTTP sessions, leases, and TTS processes close on success or failure.

## Recorded environment

| Item | Value |
| --- | --- |
| GPU / driver | NVIDIA GeForce RTX 4070 SUPER / 610.88 |
| Reported GPU memory | 12,282 MiB |
| Ollama | `qwen3.5:9b`, Q4_K_M |
| Ollama model VRAM | 5,490,081,790 bytes; about 5,235.75 MiB |
| GPT-SoVITS | Managed local v2 worker, CUDA half precision |
| Faster-Whisper | Local `small`, CPU int8 |
| Cycles / sampling interval | 3 / 250 ms |

## Measurements

| Measurement | Result |
| --- | ---: |
| Global GPU baseline / peak | 1,770 / 9,824 MiB |
| Peak capacity used / remaining | 79.99% / 2,458 MiB |
| Peak GPU utilization | 100% |
| GPT-SoVITS cold acquisition | 18.619 s |
| Ollama first-token / total p50 | 0.260 / 0.606 s |
| TTS p50 | 1.291 s for 4.06 s of audio |
| CPU STT p50 | 1.193 s |
| Owned TTS, monitor, and HTTP cleanup | 0.282 s |

| Cycle | Ollama first token | Ollama total | TTS | CPU STT |
| --- | ---: | ---: | ---: | ---: |
| 1, cold | 5.647 s | 5.780 s | 1.819 s | 2.070 s |
| 2, warm | 0.176 s | 0.501 s | 1.197 s | 1.193 s |
| 3, warm | 0.260 s | 0.606 s | 1.291 s | 1.139 s |

GPU observations are global WDDM values, including unrelated applications. Three cycles support this compatibility decision, not a statistically strong percentile claim.

The final GPU sample was 8,219 MiB because Ollama retained its model with a ten-minute keep-alive. The managed TTS process had exited. Since `/api/ps` was empty before this run, the verifier explicitly stopped the benchmark-loaded model afterward; GPU use returned to 1,770 MiB. The benchmark itself does not unload shared models.

## Cleanup evidence and limits

| Layer | Soak | Required final state |
| --- | --- | --- |
| Python transcription | 256 mixed success/failure cycles | No running, queued, occupied, or retained jobs; workers stopped |
| Python speech queue | 256 completed/cancelled turns | No turns, credits, events, clips, bytes, or callbacks; workers stopped |
| Renderer Voice | 200 alternating speech/text turns, both terminal orders | `IDLE`; no Capture/STT/Chat/Speech owner or retained transcript; hang-up clears binding |

The focused acceptance recorded 50 Python and 17 Voice-controller tests passing. The historical full regression recorded 1,761 Python passes with 4 skipped, 348 desktop protocol/controller passes, and 126 Playwright passes, alongside documentation, type, lint, build, package, and npm high-severity checks.

These deterministic tests cover ownership cleanup and late-event rejection. The physical microphone, speaker, room-echo, Narrator, and multi-hour human-call matrix was not executed; the project owner waived it as a closing requirement for this baseline. No manual pass is claimed.

## Assets and distribution

The Elysia Voice Profile remains **local-evaluation-only**. Opting in does not grant rights. Weights and recordings must stay out of Git, releases, installers, containers, mirrors, and shared diagnostics. Publication requires retained permission for the exact asset version, redistribution, modification, attribution, commercial scope, and recording/performance rights.

[MODEL_LICENSE.md](../MODEL_LICENSE.md) retains provenance, publisher names, fixed digests, restrictions, and unresolved permissions. Reviewed visual assets have separate notices; their inclusion does not authorize voice assets.

Run the repository gate from the root:

```bat
.venv\Scripts\python.exe scripts\check_distribution_assets.py
```

After packaging, inspect the unpacked tree, real ASAR listing, and extracted archive using the [desktop packaging checks](../desktop/README.md#package-and-audit). The audit freezes reviewed build inputs, verifies static asset paths/counts/bytes, and rejects private model/audio data and external desktop-pet programs. It reduces accidental distribution risk; it does not establish ownership.

The optional desktop pet runs an independently validated local program. Its configuration, executables, and dynamic assets remain outside the repository and package; implementation details are in the [desktop-pet guide](./14-LIVE2D-RUNTIME.md).

## When to repeat acceptance

Revisit the benchmark when models, quantization, context length, keep-alive, Runtime, precision, device, sentence concurrency, GPU/driver, Electron audio, queue capacity, or cancellation ownership changes.

Revisit distribution and rights review for new package inputs, dependencies carrying model/audio data, or Voice Profiles. Repeat investigation when latency or memory use becomes unacceptable, cleanup counters stay nonzero, or asset rights are disputed.
