# Agent Note: Microphone and speaker apart

Status: implemented — the message box has a microphone button and a speaker button, each switching its half of voice

## Problem

Voice is one switch ([voice conversation](../implemented/2026-11-12-voice-conversation.md)):
it opens the microphone and reads every reply aloud together. The user wants them
apart: to talk and read the answers, or to type and hear them.

## Decision

- The message box has a microphone button and a speaker button (`VoiceMode.listening`,
  `speaking`). Each turns its half on and off; either alone, or both.
- **Microphone:** Kaden listens when it is its turn to, and sends what was said.
- **Speaker:** each reply is read aloud while it streams; Esc stops the rest of that
  reply, whether it answers something said or typed (`turns.Voice` mutes until the reply
  ends).
- Each half can be turned on or off in the middle of a conversation; the other half goes
  on (`Voice.set_listening`, `set_speaking`). Turning the speaker off stops the reading
  at once. With the microphone off and nothing read, the conversation is `State.QUIET`.
  With both off, voice is off.
- `Message.spoken` says that the reply is read aloud, whichever way the message was
  written: it is what the agent's style follows (`VOICE_ON_NOTICE`: write what sounds
  right spoken; `VOICE_OFF_NOTICE` when the user reads again). A message said aloud with the speaker off is read on screen, so it is
  not `spoken`. The field and its column keep their name, so the archive's schema stays.
- The speech models are fetched and loaded together, whichever half is turned on
  first.

## Alternatives considered

- **Dictation, as Claude has it:** the microphone puts the words into the message box
  and the user sends them. It is a different thing from the turn-taking that exists; the
  user asked for a switch for each half of what is there.
- **A new message flag for "said aloud"** beside `spoken`. Nothing reads it: the agent's
  style depends only on whether the reply is heard. A new column would be a new schema
  version for nothing.
- **Load only the half in use.** With the natural voice a speaker alone would not load
  Whisper (about 1.6 GB of GPU memory). Kept for later: it splits the engine and the
  download in two, and both load at once, so it costs memory, not time.

## Consequences

- Talking and reading, or typing and listening, each without the other.
- With the natural voice, the speaker alone still holds Whisper in GPU memory, and the
  microphone alone Chatterbox.
- A message said with the microphone and read on screen gets no hint that it was spoken,
  so the agent may not allow for words the listener heard wrong.
- Checked in tests with a scripted engine; the two icons were looked at rendered. Not
  checked with a real microphone and speaker.
