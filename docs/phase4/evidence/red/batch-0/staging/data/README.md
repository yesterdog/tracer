# Staged data files, Phase 4 batch 0 (P4-00f)

Staged here; the dev moves each file to its final place later. Nothing here is under `packages/`.

## Files

| File | Final place | What it is |
|---|---|---|
| `sim_mr.json` | `packages/evalcore/tests/fixtures/sim_mr.json` | Three invented Marathi scripted call scenarios (10, 10 and 8 caller turns) with matching invented fake-agent replies, a persona block, and the character-set validator table for LLM caller lines (`charset`). |
| `README.md` | stays in the evidence tree | This note. |

## Status: needs native review

- Every line, scenario and rule in `sim_mr.json` is `provenance: synthetic_hand_written` and `needs_native_review: true`. They were written by a language model. No native speaker has read them (BL-169). Grammar, register, gender agreement and naturalness are not verified.
- No line derives from a real call. Names, numbers (840, 790, 5521, 482917, 90417, 411001, 9876543210, 30518) and addresses are invented.
- The file says nothing about the quality of any voice or model in Marathi and is not a reference for any word error rate.
- Not machine-checked in the session that wrote it (no shell was available): the JSON parse, unique ids, the `ScriptedTurn` validation and the two pytest files named in the task. The file was checked by pattern search only: ASCII only, every `\u` escape in U+0900 to U+097F, no escape in the denied ranges of the validator. The dev or qa must run `ScriptedTurn.model_validate` on every `scripted_turns` item and the charset validator on every text before relying on it.

## Shape

- `scenarios[].scripted_turns[]` is exactly `{index, text, language}` (`ScriptedTurn` forbids extra fields). `index` runs 0, 1, 2, ... without gaps, `language` is `mr`. No `{{profile.*}}` placeholders, so no `test_profile` is needed.
- `scenarios[].turn_meta[]` carries `id`, `role`, `provenance` and `needs_native_review` for each caller turn (same `index`).
- `scenarios[].agent_replies[]` is the fake agent's reply to caller turn `index`, with the same metadata.
- `scenarios[].persona` is `{language: "mr", accent: null}`: the only two fields `Persona` has besides the versioned-entity fields (id, version, digest, timestamps), which the loader supplies. The accent is `null` on purpose: nothing here supports an accent claim.
- Ids are `sim-mr-s<scenario>-c<turn>` (caller) and `sim-mr-s<scenario>-a<turn>` (agent).

## Allowed character set for LLM caller lines (`charset`)

Justification: ADR 0005 Decision 7 (native script only for configured text), ADR 0025 (the normaliser; punctuation, danda, spaces are separators and ASCII digits are the folded form of native digits) and ADR 0017 (text is stored as returned, so the validator does not normalise).

| Rule | Kind | Code points or category | Reason |
|---|---|---|---|
| V01 | allow | U+0900-U+097F | Devanagari block, includes danda U+0964 and U+0965 |
| V02 | deny | U+0966-U+096F, U+0970 | caller writes ASCII digits (judgment call, switchable) |
| V03 | allow | U+0030-U+0039 | ASCII digits |
| V04 | allow | U+0020-U+0022, U+0027-U+0029, U+002C-U+002E, U+003A-U+003B, U+003F | ASCII punctuation and space |
| V05 | deny | U+0041-U+005A, U+0061-U+007A | Latin letters: stricter than storage (judgment call) |
| V06 | deny | U+00AD, U+200B-U+200F, U+202A-U+202E, U+2060-U+2069, U+FEFF | joiners, bidi controls, invisible characters |
| V07 | deny | categories Cc, Cf, Cs, Co, Cn | controls, format, surrogates, private use, unassigned |
| V08 | require (line) | NFC | U+0958-U+095F never survive NFC |
| V09 | require (line) | at least one of U+0904-U+0939 | a line cannot be only digits or punctuation |

Deny wins over allow. Applies per line after splitting at U+000A. The end sentinel (SCOPE Q9) is matched exactly before this table. Passing says nothing about Marathi quality.

## Sarvam text-to-speech facts for `mr-IN` (documentation only)

Retrieved 2026-10-27. No Sarvam API call was made; no key was used or looked for. Each cell is what the page states; "not verified" means the pages read do not state it. Nothing below is an accent or quality claim by Tracer.

| Item | Stated by the documentation | Source |
|---|---|---|
| Models | `bulbul:v2`, `bulbul:v3`, `bulbul:v4-flash`; `bulbul:v3` is used when `model` is omitted | https://docs.sarvam.ai/api-reference-docs/text-to-speech/convert |
| `mr-IN` as `language_code` | listed; the page also lists en-IN, hi-IN, te-IN, ta-IN, kn-IN, ml-IN, pa-IN, gu-IN (and codes for languages outside Tracer's configured set) | same; https://docs.sarvam.ai/api-reference-docs/getting-started/models/bulbul (Bulbul v3 supports `mr-IN`) |
| What the language code does | "primarily used by the pre-TTS text normalization model" | bulbul model page |
| `bulbul:v3` speaker names | shubh (default), aditya, ritu, priya, neha, rahul, pooja, rohan, simran, kavya, amit, dev, ishita, shreya, ratan, varun, manan, sumit, roopa, kabir, aayan, ashutosh, advait, anand, tanya, tarun, sunny, mani, gokul, vijay, shruti, suhani, mohit, kavitha, rehan, soham, rupali (37 names; the page text says "30+") | convert page; bulbul model page |
| `bulbul:v2` speaker names | anushka (default), manisha, vidya, arya, abhilash, karun, hitesh | convert page |
| `bulbul:v4-flash` speakers | personas named `<name>_<language>_<style>`; the voices guide has a heading "Marathi (mr-IN) (9 voices)" naming Ishita, Mrunal, Neha, Nilesh, Ritu (two entries), Rupali, Soham and Mukul as first names. Full IDs not read, except `ritu_mr_narration`, shown as an example on the convert page. | convert page; https://docs.sarvam.ai/api/api-guides-tutorials/text-to-speech/voices |
| Speaker names | lowercase, case-sensitive; "Speaker selection must match the chosen model version" | convert page; voices guide |
| Which `bulbul:v3` speaker supports `mr-IN` | not verified: the convert page and the bulbul page state no speaker-to-language mapping | convert page; bulbul model page |
| Marathi-specific voice recommendation | not verified. A search-result summary mentioned recommendations on the voices guide; the page text read did not state them. Tracer makes none. | voices guide |
| Voice characteristics for Marathi | not verified (no description). The voices guide has Marathi audio samples for shubh and ishita, with no description. It says "voice quality varies by language". | voices guide |
| Accent of any voice | not verified: not stated for any voice | all pages read |
| Sample rates (Hz) | 8000, 16000, 22050 (default), 24000, 32000, 44100, 48000; the last three are REST only for v3 and v4-flash, not streaming | convert page |
| Text limit | 2500 characters (v3, v4-flash); 1500 (v2) | convert page |
| Pace | 0.5 to 2.0 (v3, v4-flash) | convert page |
| Whether a v3 short name is accepted by v4-flash | not verified (a search-result summary says it is rejected with 400; not read on a page) | - |
| Pricing, rate limits, terms of use | not verified (owner decision O1 on Sarvam terms is open) | - |

Checks the framework may make from this table (SCOPE Q7): the requested `model` is one of the three names, the requested `speaker` is in the list for that model, and `language_code` is `mr-IN` for a Marathi persona. It may not infer an accent, a quality or that a speaker works for Marathi; the persona-to-voice mapping is explicit in the sim profile.

Pages read: the convert page and the bulbul model page (both fetched on 2026-10-27), and the voices guide page (fetched 2026-10-27). One guessed URL (`.../text-to-speech/voices` under `api-reference-docs`) and the `bulbul-v4-flash` model page returned "page not found"; their content is not used. The docs pages disagree on speaker lists in places (search summary), so re-fetch before relying on a list.
