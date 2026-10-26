# tracer-evalcore

Pure core of the recorded-call scoring in Tracer: the WAV reader, the VoicEra export reader, the
deterministic audio chunker, the Marathi comparison normaliser `mr-Deva/1`, the word alignment, the
disagreement spans and flags, the shipped metric definitions, the rubric file, the result mapping
and the pipeline. It imports only the standard library and `tracer-evalschema`; it has no network
access and no clock. What the numbers mean and do not mean is in
`docs/concepts/recorded-call-scoring.md`; changes are in `CHANGELOG.md`. Status: 0.0.1, unreleased.
