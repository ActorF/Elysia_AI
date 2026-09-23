# Voice Performance, Safety, and Rights Acceptance

> Acceptance date: 2026-09-23
> Scope: local Ollama + managed GPT-SoVITS + Faster-Whisper voice pipeline
> Result: accepted for the current local-first development baseline, subject to the limitations below

## 1. What was accepted

This acceptance closes the performance, cleanup, and voice-asset distribution work for the current Voice implementation. It establishes four concrete facts:

1. the configured Ollama model and managed GPT-SoVITS worker can run together on the target GPU while Faster-Whisper runs on CPU;
2. repeated STT, speech-queue, and Renderer Voice turns release their owned jobs, buffers, credits, callbacks, and worker threads;
3. the selected Elysia Voice Profile has recorded provenance but still lacks adequate redistribution permission, so it remains local-evaluation-only; and
4. Git, Electron Builder configuration, CI packaging, and produced package trees have automated checks that reject local weights, reference audio, caches, runtime data, and packaging escape routes.

This is an engineering acceptance, not a legal opinion or a promise that every computer will have the same latency.

## 2. Resource policy

The supported topology is deliberately asymmetric:

```text
Ollama on GPU ───────┐
                    ├─ overlap during reply generation and speech synthesis
GPT-SoVITS on GPU ──┘

Faster-Whisper on CPU
  └─ runs after the measured GPU inference pair under the existing Backend exclusion policy
```

- `TRANSCRIPTION_DEVICE=cpu` is now the safe default. This reserves GPU capacity for Ollama and GPT-SoVITS. `auto` and `cuda` remain explicit, restart-bound choices for machines that have measured sufficient capacity.
- The managed GPT-SoVITS worker is acquired once for the Backend lifetime. Recreating it for every sentence would repeatedly pay the measured cold-start cost and introduce extra process/guard races.
- Ollama owns its shared model-residency policy. Elysia closes its HTTP client but does not silently unload a model that another local client may be using.
- Speech and transcription remain bounded queues. Cancellation releases logical ownership immediately while native work retains physical capacity only until its bounded call returns.

## 3. Reproducible benchmark

The benchmark accepts only bounded numeric controls. Test Prompt, synthesized WAV, and transcript are fixed internally, remain in memory, and are never included in JSON output.

Prerequisites:

- Windows with `nvidia-smi.exe` and the configured local Ollama model;
- a complete local Faster-Whisper model;
- a reviewed local Voice Profile and explicit `GPT_SOVITS_ALLOW_LOCAL_EVALUATION=True` opt-in;
- ignored GPT-SoVITS Runtime, weights, and reference audio matching that Profile.

Run from Command Prompt:

```bat
cd /d D:\Elysia_AI
.venv\Scripts\python.exe scripts\benchmark_voice_pipeline.py --cycles 3 --sample-interval-ms 250 --request-timeout-seconds 180
```

The command applies a hard wall-clock deadline and response-size limits to local Ollama calls, samples global GPU state, overlaps Ollama streaming with managed TTS, then sends the in-memory TTS WAV through CPU STT. Every owned monitor, HTTP session, TTS lease, and TTS Runtime is closed even when a measured operation fails.

## 4. Measured result on the acceptance machine

Environment:

| Item | Value |
| --- | --- |
| GPU | NVIDIA GeForce RTX 4070 SUPER |
| Driver | 610.88 |
| Reported GPU memory | 12,282 MiB |
| Ollama model | `qwen3.5:9b`, Q4_K_M |
| Ollama reported model VRAM | 5,490,081,790 bytes, about 5,235.75 MiB |
| GPT-SoVITS | managed local v2 worker, CUDA half precision |
| Faster-Whisper | local `small`, CPU int8 policy |
| Cycles / GPU interval | 3 / 250 ms |

Aggregate result:

| Measurement | Result |
| --- | ---: |
| Global GPU baseline | 1,770 MiB |
| Global GPU peak | 9,824 MiB, 79.99% of reported capacity |
| Remaining capacity at observed peak | 2,458 MiB |
| Peak GPU utilization | 100% |
| GPT-SoVITS managed-worker cold acquisition | 18.619 s |
| Ollama first-token p50 | 0.260 s |
| Ollama total elapsed p50 | 0.606 s |
| GPT-SoVITS synthesis p50 | 1.291 s for 4.06 s of audio |
| CPU Faster-Whisper p50 | 1.193 s |
| Owned TTS/monitor/HTTP cleanup | 0.282 s |

Per-cycle measurements:

| Cycle | Ollama TTFT | Ollama elapsed | TTS elapsed | CPU STT elapsed |
| ---: | ---: | ---: | ---: | ---: |
| 1, cold Ollama load | 5.647 s | 5.780 s | 1.819 s | 2.070 s |
| 2, warm | 0.176 s | 0.501 s | 1.197 s | 1.193 s |
| 3, warm | 0.260 s | 0.606 s | 1.291 s | 1.139 s |

The GPU numbers are global WDDM observations and include the desktop plus unrelated GPU applications; they are intentionally not presented as per-process attribution. Three samples are sufficient for this compatibility decision, not for a statistically strong percentile claim—the three-cycle p95 equals the maximum and should not be generalized.

The benchmark's final GPU sample was 8,219 MiB because its Ollama request deliberately used a ten-minute keep-alive and the shared model remained resident. The managed GPT-SoVITS process was absent after cleanup. Because `/api/ps` was empty before this acceptance run, the verifier explicitly stopped the benchmark-loaded Ollama model afterward; `/api/ps` returned no models and global usage returned to the 1,770 MiB baseline. The benchmark itself does not unload shared Ollama state because doing so could disrupt another local client.

## 5. Long-session cleanup evidence

The automated soak checks exercise lifecycle ownership rather than performing hundreds of expensive model inferences:

| Layer | Soak | Required final state |
| --- | --- | --- |
| Python transcription jobs | 256 mixed success/failure cycles | zero running, queued, occupied, or retained jobs; every worker stopped |
| Python speech queue | 256 completed/cancelled turns | zero active/retained turns, sentence credits, notifications, delivery events, clips, bytes, or callbacks; worker/notifier/aborter stopped |
| Renderer Voice controller | 200 alternating speech/text turns with both terminal orders | `IDLE`, no Capture/STT/Chat/Speech owner, no retained transcript or PCM-related state; final Hang-up clears binding |

Focused acceptance completed with 50 Python tests and 17 Voice-controller tests passing. These deterministic tests cover cleanup invariants and late-event rejection; they do not claim that a person completed a multi-hour call on physical devices. The project owner waived the manual microphone/speaker/room/Narrator matrix as a closing requirement, so those observations remain unclaimed.

The final repository regression run additionally passed 1,761 Python tests with 4 skipped, 348 desktop protocol/controller tests, and 126 Playwright UI tests. Python and desktop documentation audits, mypy, ESLint, TypeScript checks, the production build, the Windows package build, and the npm high-severity audit also passed.

## 6. Voice rights decision

The exact selected checkpoint and reference-audio digests, named publisher/integration source, supplied restrictions, character/performance boundary, and unresolved authorization gap are recorded in [`MODEL_LICENSE.md`](../MODEL_LICENSE.md).

The operative decision is:

- the Elysia Voice Profile remains `local-evaluation-only`;
- a local opt-in enables technical evaluation but does not create permission;
- checkpoints and reference recordings must not enter Git, GitHub Releases, installers, containers, mirrors, or shared diagnostic archives; and
- publication remains blocked until retained permission covers the exact asset version, redistribution, modification, attribution, commercial scope, and applicable recording/performance rights.

The separately tracked official Elysia signet branding remains governed by its own notice and project-owner decision. Allowing that reviewed PNG/ICO does not authorize voice assets.

## 7. Distribution enforcement

Run the repository check before every change and the artifact check after every package build:

```bat
cd /d D:\Elysia_AI
.venv\Scripts\python.exe scripts\check_distribution_assets.py
cd desktop
npm run package
npx --no-install asar list out\win-unpacked\resources\app.asar > "%TEMP%\elysia-asar-listing.txt"
cd ..
.venv\Scripts\python.exe scripts\check_distribution_assets.py --unpacked-tree desktop\out\win-unpacked --asar-listing "%TEMP%\elysia-asar-listing.txt"
del "%TEMP%\elysia-asar-listing.txt"
```

The checker rejects forbidden Git paths/extensions and freezes the complete reviewed Electron Builder configuration. This prevents inherited configuration, platform-specific `files`, alternate app roots, hooks, custom Electron distributions, NSIS scripts, `extraResources`, `extraFiles`, and `asarUnpack` from silently expanding package input. GitHub Actions also builds the unpacked Windows application, captures the real ASAR listing, and audits both layers. The current Git index and local `desktop\out\win-unpacked` scan pass with no local model or reference-audio assets included.

This gate is defense in depth, not proof of ownership. Reviewers must still examine newly introduced data, generated bundles, and third-party dependencies.

## 8. Revisit triggers

Repeat the benchmark and reconsider the policy when any of these changes:

- Ollama model, quantization, context length, or keep-alive policy;
- GPT-SoVITS Runtime, checkpoint, precision, device, or sentence concurrency;
- Faster-Whisper model, compute type, or device;
- GPU/driver, Electron audio path, queue capacity, or cancellation ownership;
- a new package input, dependency containing model/audio data, or Voice Profile; or
- observed peak approaches capacity, latency becomes unacceptable, cleanup counters do not return to zero, or a rights claim is disputed.
