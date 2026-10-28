# Synthetic Marathi inputs for the offline `tracer simulate` quick starts (P4-37, P4-J30)

Provenance: `synthetic_hand_written`. `needs_native_review`: true. Every line of text is invented (generic call-centre Marathi, the scenario `sim-mr-s1` of `packages/evalcore/tests/fixtures/sim_mr.json` and the golden set of `packages/evalcore/tests/fixtures/golden_mr.json`). Nothing derives from any real call. No native speaker has reviewed any word (BL-169). These files are plumbing data: they show the shape of the inputs and let the commands run with no key and no network. They say nothing about Marathi, about any agent or about any provider.

The quick starts are in [packages/cli/README.md](../../../packages/cli/README.md), and the pages that say what the commands report and do not claim are [simulated calls](../../concepts/simulated-calls.md) and [LLM judge](../../concepts/llm-judge.md).

## Files (all under this directory)

| File | Holds |
|---|---|
| `evaluator.json` | `tracer.sim_evaluator/1`: the ten scripted caller lines of `sim-mr-s1`, exactly the fixture's `scripted_turns` (a test compares them) |
| `persona.json` | `tracer.sim_persona/1`: the caller `caller-a`, language `mr`, no accent |
| `sim-profile.json` | `tracer.sim_profile_file/1`: the dialect `pipecat-protobuf/1`, the audio format, the silence values, the fake voice and the fake speech-to-text; every value is written here because the command has no default |
| `fake-agent.json` | `tracer.fake_agent/1`: the offline fake agent, a list of waits and replies (the replies are the `exact_facts` plants of the golden fixture, one per question); no socket is opened |
| `golden-set.json` | `tracer.golden_set/1`: the ten items of the golden fixture, exactly its `golden_set` member (a test compares them) |
| `judge-config.json` | `tracer.judge_config/1` for the engine `fake/1`: the model name is a label, there is no endpoint |
| `fill_labels.py` | stands in for a human labeller in the quick start: it writes a labels file in which every item but the last is `agrees`; standard library only |

JSON is ASCII-only (`\uXXXX`), as in the other example directories.

## What the fake engines do

- The fake agent speaks its scripted replies after each caller line; the fake speech-to-text returns the words of those replies; the fake text-to-speech makes a tone for each line. The timings of the run are read from the simulator's real clock, so they differ from run to run.
- The fake judge (`--judge fake:agrees`) reads nothing: it answers the same for every item, whatever the agent said. Its output shows the shape of the judge lines and the label loop, not a judgement of anything.
- A run needs about twenty seconds, because the simulator paces the caller's frames in real time.
