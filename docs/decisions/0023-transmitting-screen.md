# 0023: Transmitting screen and word timings

- **Status:** accepted
- **Date:** 2026-10-25
- **Ticket:** STU-116

## Context

Screen 3 of the app design is Alfred's reply. It shows:

- a header with a close button and an "ALFRED · TRANSMITTING" pill
- the orb, speaking
- the reply, karaoke-style: words already heard bright, the word being spoken (and values like times) in accent, the rest dim
- a slot for result cards (STU-117)
- a CUT IN pill, plus mute-mic and send icons

The highlight has to follow the audio, so the server must say when each word is heard. Until now it sent the reply text (`reply.text.delta`) and the audio, with nothing tying the two together.

## Decision

### Word timings are estimated, per segment

Neither engine reports word timing here. kokoro-onnx has `create_timed`, but it needs a model export with a `duration` output. Our `kokoro-v1.0.onnx` only outputs `audio`. Piper reports nothing.

So `LocalTTS` estimates timings for each synthesized segment (a sentence, or the first clause):

1. `speech_bounds` finds where speech starts and ends in the rendered audio, from 10 ms frames whose RMS is above 5 % of the loudest. This drops Piper's lead-in and any quiet tail.
2. `estimate_word_timings` shares that span out over the segment's whitespace-separated words in proportion to their letters:
   - each digit counts 3.5 letters ("3:45" is "three forty-five")
   - a sentence end adds a gap worth 4 letters after the word
   - a comma, semicolon, colon or spaced dash adds a gap worth 1
   - tokens with nothing speakable ("—") add only their gap
3. Words with digits or £ $ € %, and am/pm/o'clock/percent right after a number, get `emphasis: true`.

The weights were fitted against Whisper `small.en` word timestamps on four typical replies, spoken by both Kokoro (`bm_george`) and Piper (`en_GB-alan-medium`), 122 words:

- **Mean start error 0.19 s, p90 0.39 s.** A word averages about 0.3 s, so that's within about one word, which is what the ticket asks for. Whisper's own timestamps are only good to about 0.1 s.
- Error can't build up across a reply. Each segment is timed against its own audio, and the splitter cuts the first segment at a clause.

Real alignment would come from a Kokoro export with durations (kokoro-onnx's `scripts/export.py`). That's a follow-up, not needed for this ticket.

### Protocol: `reply.words`

Before each segment's audio frames, the gateway sends:

```
reply.words {turn_id, words: [{word, start_ms, end_ms, emphasis?}]}
```

Times are ms from the first sample of the turn's reply audio. The gateway offsets each segment by the audio already sent, including the 100 ms pause after each segment. `emphasis` appears only when true. It's a new message type, so older apps ignore it, and there's no version bump. The `TextToSpeech` protocol is now `synthesize_segments`, which yields `SpokenSegment(pcm, words)`. `LocalTTS.synthesize` still yields plain PCM chunks for the scripts.

Places as emphasis ("Wayne Tower") would need entity recognition, so they're out of scope.

### The app follows the audio it hears

`AudioPlayer.position` is the ms of reply heard so far. It's computed from the same presentation timestamp as `level` (output latency included), polled every 20 ms.

`VoiceController` collects `reply.words` into `VoiceState.words`. The words are the reply's own tokens, so `alignWords` finds each one in the reply text in order. The highlight is picked by `highlightFor`:

| State | Highlight |
| --- | --- |
| Thinking, no timings yet | all dim (nothing has been said) |
| Thinking or speaking, with timings | **follow audio**: heard words bright (values accent), the current word accent, the rest dim |
| Speaking without timings (an older server) | everything bright as it arrives: the ticket's fallback |
| Finished | everything bright, values accent |

The reply text recomposes only when the current word changes (`derivedStateOf`), not 50 times a second. The line being spoken stays in view, with one heard line above it. `TransmittingViewModel.heard` never goes backwards within a turn, because the player's position drops to 0 as playback ends, just before the turn does.

### A layer, like Recording

`TransmittingScreen` is drawn over the app (`AlfredApp.kt`, under Recording) from the moment a turn leaves listening (Thinking) until **2 s after it ends**. The hold lets the last words be read, and shows the error if the turn failed. Then Home comes back. STU-117's result cards should keep it up while they're shown.

- **Close / back:** `VoiceController.cancel()` and straight back to Home.
- **CUT IN:** `VoiceController.cutIn()`. It stops playback at once (`AudioTrack.pause/flush` on the calling thread), sends `turn.cancel` (no new `turn.interrupt` message, since `turn.cancel` already stops a turn wherever it is), and starts listening with the same go-quiet end-of-speech as push-to-talk (`goQuiet()`, now in `EndOfSpeech.kt`). Recording takes over. With no link, playback still stops and the hold shows "Not connected to the server".

### Buttons left out

Dan asked for buttons only where needed, so these design icons aren't built:

- **Screen icon:** turn details already live on Home's crosshair sheet (STU-114).
- **Send icon:** no agreed job yet.
- **Mute mic:** it would do nothing until follow-up mode exists. STU-72 should add it.

## Consequences

- Highlight accuracy is that of the estimate: about half a word on average, worse inside long words with silent letters or unusual numbers. Dan needs to check it on a phone over Tailscale. If it isn't good enough, the fix is the Kokoro duration export, with no protocol change.
- `emphasis` is a heuristic for numbers only.
- Screenshot test `TransmittingScreenshotTest` (speaking, thinking) uses a plain `Application`, as `HomeScreenshotTest` does.
