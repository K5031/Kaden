# Agent Note: A natural voice on the GPU

Status: implemented — with an NVIDIA GPU and the optional `voice-gpu` install, Kaden speaks with Chatterbox, in its own voice or one copied from a recording; elsewhere with Kokoro

## Problem

[Voice conversation](2026-11-12-voice-conversation.md) speaks with Kokoro
(82M parameters) on the CPU, so that it works on any machine. It is clear but flat, and
its voices are a fixed set; the user finds it does not sound real and the choice too
small. Their machine has an NVIDIA GPU (RTX 5060, 8 GB), which runs far better open
models at speaking speed.

## Decision

- **Chatterbox** (Resemble AI, open source, MIT, `voice/natural.py`) speaks where the
  `voice-gpu` extra is installed (`chatterbox-tts`, which brings PyTorch with CUDA).
  Otherwise Kokoro speaks. Silero hears when the user talks either way; `SherpaEngine`
  hands speaking to a `Speech` (`Kokoro` or `Chatterbox`) and transcribing to a
  `Listener` (`Parakeet`, or [Whisper on the GPU](2026-11-23-whisper-listens-on-the-gpu.md)
  with the natural voice).
- **Its full English model**, so Kaden speaks English only: the multilingual model was
  [tried for Chinese and dropped](../rejected/2026-11-22-talking-in-chinese.md), its
  English sounding worse to the user.
- **Python 3.14.** RTX 50 GPUs need PyTorch 2.7 or later, which `chatterbox-tts` allows on
  Python 3.14 only (it holds PyTorch at 2.6 below that). The extra installs nothing on
  older Pythons, so there Kaden speaks with Kokoro.
- **Its weights** (3.2 GB) are fetched into Kaden's voice folder with the other speech
  models and the same question, not into Hugging Face's cache. Kokoro's model is not
  fetched where Chatterbox speaks.
- **Voices** with Chatterbox: its own (**Natural**), and any copied from a recording the
  user adds in Settings → General → Voice → Add…, kept in the voice
  folder under the file's name. With Kokoro, its five named voices.
- On an RTX 5060, it makes speech about twice as fast as it plays, in 3.7 GB of GPU
  memory; as each sentence is made while the one before plays, only the first of a reply
  is waited for. Sentences are spoken as each is
  ready, as with Kokoro.
- Chatterbox sometimes repeats a word or phrase, or adds sounds like "hmm" (its English
  model has no guard against it): each sound it makes is heard back by Whisper and made
  again when it strays from its text.
- Chatterbox's watermark (Perth, inaudible) stays on.

## Alternatives considered

- **Chatterbox Turbo** (a smaller text model and a one-step sound decoder). On the user's
  GPU it made speech 4.2 times faster than it plays, against 1.9 times, in 3.3 GB against
  3.7 GB; the user found it sounded worse. Once sentences are made while the one before
  plays, the full model's speed only shows in the first sentence of a reply.
- **A cloud voice** (ElevenLabs, OpenAI). The most natural, but a second key, a cost per
  reply, and the reply text going to another company; the voice note rejected it for
  the same reasons.
- **Orpheus or another 3B speech model.** Very expressive, but 3B parameters is tight on
  8 GB beside other work, and slower to first sound.
- **Kokoro's other voices.** No install, but the same flat model.
- **Chatterbox for everyone, in the desktop extra.** Several GB of PyTorch on machines
  that cannot run it at speed.

## Consequences

- A natural voice, and any voice the user has a recording of, at no cost per reply and
  with nothing sent away.
- The extra is a few GB of PyTorch, a fast-moving dependency, and ties the install to
  Python 3.14.
- Whether CUDA works is known only when voice mode loads: with the extra but no usable
  GPU, voice mode says so and does not start, where Kokoro would have worked.
- Copying a voice from a recording copies anyone's voice: the user is the one who
  chooses the recording.
