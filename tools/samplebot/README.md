# tools/samplebot

Pipecat 1.14.0 sample agents for the loopback interop tests (P4-26, P4-27). Its own uv project:
not a member of the root workspace, not in the root `uv.lock`, not in any tracer wheel, never
imported by a tracer package. Its closure holds LGPL items (tool-only): see
`docs/security/P4-02-licenses.md`. The lock arrives with the bots (P4-26).
