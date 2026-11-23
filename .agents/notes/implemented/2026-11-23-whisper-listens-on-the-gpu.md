# Agent Note: Whisper listens on the GPU

Status: implemented — with the natural voice, Whisper large-v3-turbo on the GPU understands what the user says, in place of Parakeet on the CPU

## Problem

[Voice conversation](2026-11-12-voice-conversation.md) turned speech into text with
Parakeet TDT 0.6b v2, 8-bit, on the CPU. The user found it got their words wrong.
Parakeet does best on clear, standard English; names, casual speech and accents throw it
more than models trained on more varied speech.

## Decision

- Where the [natural voice](2026-11-22-natural-voice-on-the-gpu.md) speaks (the
  `voice-gpu` extra and an NVIDIA GPU), Whisper large-v3-turbo (OpenAI, MIT, 809M
  parameters) transcribes (`natural.Whisper`), on the GPU in half precision, through
  `transformers`, which that extra already installs. English is set, not detected.
- Silero VAD still hears when the user starts and stops talking; only the transcribing
  changes. Without the natural voice, Parakeet stays.
- **Not speech, dropped:** Whisper writes something even for noise ("so", "Okay.",
  "you"), but unsure of it. What it writes with an average log-probability below −0.8
  is dropped. The turbo model's own no-speech probability stays at zero even for silence,
  so it cannot serve.
- Its weights (1.6 GB) are fetched into the voice folder with the other models, under
  the same question; Parakeet's are not fetched where Whisper listens.

## Trial

On the user's own voice, read aloud: the two agreed almost word for word; Whisper wrote
"Kaden" and "25" where Parakeet wrote "Caden" and "twenty four", and each slipped once.
Whisper took 0.3 s on the GPU for 21 s of speech, Parakeet 0.5 s on the CPU. On 24 s of
no talking, Whisper wrote short words without the guard and nothing with it.

## Alternatives considered

- **A bigger Whisper on the CPU, through sherpa-onnx.** No GPU needed, but several
  seconds for each thing said, where the GPU takes well under one.
- **faster-whisper (CTranslate2).** Faster than `transformers`, but a new library with its
  own CUDA build, where `transformers` and PyTorch are already installed.
- **Parakeet unquantized, or with hotwords.** A small gain on the same model; hotwords
  help with names only.

## Consequences

- Names and numbers come out better, and casual speech should too; on clear read speech
  the gain is small.
- About 1.7 GB more GPU memory beside Chatterbox, about 5 GB in all, and a 1.6 GB
  download.
- The confidence cut-off was set on one user's recordings; a mumbled real answer can
  fall under it and be dropped as noise.
