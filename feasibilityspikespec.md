# Feasibility Spike — "Can we pull the numbers?"

**Owner:** you (or one dev) | **Time:** ~half a day | **Cost:** < $5 in API calls | **PHI:** none — synthetic audio only
**The one question it answers:** *before* building anything, can a pipeline reliably extract SUDS/VOC/phase from a therapy session and attribute couples dialogue to the right partner — from **audio**, not just clean text?

This is a go/no-go, not a product. Throwaway code. You are buying one decision: **build, or don't.**

---

## The decomposition (why this test is shaped this way)

The moat rests on two hard capabilities. This spike splits each into its two failure points so you learn *which* half breaks:

```
        AUDIO  ──►  [ ASR + diarization ]  ──►  transcript+speakers  ──►  [ LLM extraction ]  ──►  structured note
                          (risk A)                                              (risk B)
```

- **Risk B (LLM extraction)** — already looks solved. A frontier model pulled every field cleanly from the vignette *text* (you saw it). This spike confirms it on your own stack, but expect a pass.
- **Risk A (ASR + diarization)** — the real unknown. Does speech-to-text hear "SUDS is an eight" as `8`? Does diarization keep Sam and Dev straight, especially at similar pitch? **This is what you're actually testing.**

If the whole thing works on text but breaks on audio, you haven't failed — you've localized the problem to a swappable vendor component, which is a far cheaper fix than "the idea doesn't work."

---

## Steps (half a day)

1. **Text baseline (30 min).** Run the extraction prompt (below / in `spike_harness.py`) on the *clean* vignette transcripts. Confirm it returns the answer-key values. Expected: pass. If it fails here, fix the prompt before touching audio.
2. **Record audio (30 min).** Read both vignette scripts aloud, record as `.wav`/`.mp3`. Couples: record once with distinct voices, once with similar-pitched voices.
3. **Wire ASR + diarization (1–2 hr).** Pipe the audio through a speech-to-text service with speaker labels (options below). Output a speaker-labeled transcript.
4. **Run extraction on the ASR transcript (30 min).** Same prompt, now on the machine transcript instead of the clean one.
5. **Grade (30 min).** Auto-check the objective fields (numbers, phase, pursuer/withdrawer); eyeball the subjective ones. Compare audio-path score to text-path score. The **gap between them is Risk A quantified.**

---

## Tooling (pick one per box — all have free tiers)

| Component | Options | Note |
|---|---|---|
| **ASR + diarization** | Deepgram (`nova-3`, `diarize=true`), AssemblyAI (speaker labels), or **WhisperX** (open-source: Whisper + pyannote) for $0 | WhisperX runs locally — best for a no-cost, no-BAA spike |
| **LLM extraction** | Any frontier model API (Claude, GPT-4-class) with JSON/structured output | The `spike_harness.py` defaults are marked with TODOs for your key |
| **Grader** | `spike_harness.py` (provided) | Auto-scores numbers + attribution; flags text fields for your eyeball |

**HIPAA note:** because the audio is fictional, no BAA is needed *for the spike*. For production, every ASR/LLM vendor in the path must be HIPAA-eligible under a signed BAA — and consumer API tiers often are **not** BAA-covered. Don't let a spike vendor silently become the production vendor.

---

## Pass / fail gate

**PASS (proceed to build) =** on the **audio** path, both SUDS (8→2) *and* both VOC (3→6) come back exact, phase = 4, **and** pursuer=Sam / withdrawer=Dev are correct — reproducibly across a few runs.

**Decision tree on the result:**
- **Passes on text, passes on audio →** core de-risked. Build. Risk A is not your enemy.
- **Passes on text, numbers break on audio →** ASR mis-hears digits. Try a better ASR model, or add the fallback below. *Fixable, not fatal.*
- **Passes on distinct voices, attribution collapses on similar voices →** diarization is your bottleneck. This is the known hard edge — scope couples/family carefully and consider capture-side fixes (per-speaker mics, channel separation). *Fixable with effort.*
- **Fails on clean text →** prompt/model problem (unlikely given the inline demo). Fix the prompt; don't blame the model yet.

**Honesty on rigor:** one vignette pair is a *smoke test*, n=1. A real go/no-go gate wants 3–5 varied recordings (different accents, pacing, a messy overlapping couple). But a single clean pair will expose a *hard* failure immediately, which is all a half-day spike needs to do.

---

## The product insight this spike should give you

The scariest technical dependency — "what if ASR mangles a spoken number?" — has a cheap product answer you can design in from day one: **don't rely solely on extraction for the numbers.** Two hedges:
1. **Prompt the clinician's phrasing:** train users to state ratings cleanly ("SUDS is now a two") — trivial habit, big accuracy gain.
2. **One-tap structured input:** a lightweight in-session control to log SUDS/VOC with a tap, so the rating is *captured structurally* and the transcript just corroborates it.

That converts your highest-risk extraction target into a UX choice — and it's a feature the generic scribes, built for passive capture, don't have. The spike tells you how hard you *need* to lean on hedge #2.

---

## Extraction prompts (also in `spike_harness.py`)

### EMDR — system prompt
> You are a clinical documentation extractor for EMDR therapy sessions. From the transcript, extract ONLY what is explicitly stated. Return strict JSON matching the schema. **Do not infer or invent numbers** — if a SUDS or VOC value is not spoken, return null. Capture cognitions in the client's own words.
>
> Schema: `{ target_memory, image, negative_cognition, positive_cognition, voc_pre (1–7), voc_post, suds_pre (0–10), suds_post, phase (1–8), bls_type, bls_sets, imagery_shift, closure_method, plan_next_target, emotions_body }`

### Couples — system prompt
> You are a clinical documentation extractor for couples therapy. Speakers may be labeled generically (Speaker 0/1). Map each speaker to their name if stated, then attribute positions correctly. Return strict JSON. Do not merge the partners into one summary; preserve who said what and name the interactional cycle.
>
> Schema: `{ speakers:[{label, inferred_name}], pursuer, withdrawer, presenting_issue, cycle_named, intervention, partner_shifts, attributions:[{statement, speaker}], risk_screen }`

The companion `spike_harness.py` contains these prompts, both JSON schemas, the vignette answer keys, and an auto-grader that prints a scorecard.
