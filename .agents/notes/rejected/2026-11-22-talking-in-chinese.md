# Agent Note: Talking in Chinese

Status: rejected — Chatterbox's multilingual model speaks Chinese, but its English sounded worse to the user than the English model's, and they chose English

## Problem

[Voice conversation](../implemented/2026-11-12-voice-conversation.md) is English only:
Parakeet understands only English, and the [natural voice](../implemented/2026-11-22-natural-voice-on-the-gpu.md)
speaks with Chatterbox's English model. The user wanted to talk with Kaden in Chinese.

## Proposal

- With the natural voice, SenseVoice Small (sherpa-onnx, CPU, 163 MB) listens in Mandarin,
  Cantonese, English, Japanese and Korean, and Chatterbox's multilingual model speaks.
- No language setting, as in Claude's voice mode: each sentence spoken in the language its
  script shows (kana Japanese, Hangul Korean, Chinese characters Chinese, else English).
- Chinese, Japanese and Korean kept when read aloud, and `。！？` end a sentence.

## What the trials showed

On an RTX 5060, with Kaden's own download and engine code:

- **Multilingual V2** (`t3_mtl23ls_v2`, the only one the PyPI release 0.1.7 loads) often
  failed to stop after a short Chinese sentence: in about a third of runs it went on
  babbling for 15 to 40 s. Lower CFG, temperature or exaggeration did not help.
- **Multilingual V3** (`t3_mtl23ls_v3`, loadable only from Chatterbox's GitHub code at the
  time) stopped in every run; about 1 s per sentence, 18 s to load, 4.3 GB of GPU memory.
  SenseVoice transcribed its Chinese and English back almost word for word.
- Played side by side, the user found the English model's English better than V3's.

## Alternatives considered

- **Both models, by language** (English model for English, V3 for Chinese). Together
  about 7.7 GB of GPU memory, more than the 7.6 GB card; at half precision, untested
  quality; swapped in and out, a 15 s pause at each change of language. Not wanted.
- **V3 for everything.** Chinese, at the cost of the English the user prefers.

## Consequences

- Voice conversation stays English only, with the English model's voice.
- Should Chinese come back: take V3 (not V2), and either a GPU with room for both models
  or the user's acceptance of V3's English.
