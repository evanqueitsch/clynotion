# Spike result — GO

**Verdict:** Passes on text **and** audio → core de-risked. **Build.**

| Path | Gate | Result |
|---|---|---|
| EMDR text | SUDS 8→2, VOC 3→6, phase 4 | PASS |
| Couples text | pursuer=Sam, withdrawer=Dev | PASS |
| EMDR audio (Whisper ASR → Claude) | same numbers + phase | PASS |
| Couples distinct-voice ASR | pursuer/withdrawer | PASS |
| Couples similar-voice ASR | pursuer/withdrawer | PASS |

## Risk A (ASR)
Whisper `base` preserved spoken digits (`Voc is a 3`, `Suds is an 8`, `Suds is now a 2`, `Voc is a 6`) and phase. Couples roles survived via therapist naming even without diarization.

## Caveats (n=1 smoke)
- Synthetic SAPI audio, not real clinic acoustics / overlap / accents.
- No Deepgram diarization — attribution relied on names + pronouns in the script.
- ASR mangled “EFT” (`F lens` / `effed`); model still inferred intervention from context.
- Prompt needed an explicit “phase must be JSON integer” constraint (fixed in harness).

## Hedge signal
Numbers survived clean clinician phrasing. Still design one-tap SUDS/VOC capture as insurance for messy real sessions.
