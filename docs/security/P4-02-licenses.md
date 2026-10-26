# P4-02 licence review of the Phase 4 dependencies (security-licensing)

Date of lookups: 2026-10-27. Scope: task P4-02 of `docs/phase4/SCOPE.md` (findings F3 and F9, section C questions Q2 and Q5 to Q7, the dependency tables, gate item 9). Reviewed BEFORE any Phase 4 dependency is added. Nothing in the repository was changed except this file and the proposed policy patch `docs/phase4/evidence/red/batch-0/staging/license-policy.proposed.toml` (nothing applied to `docs/security/license-policy.toml`, `pyproject.toml`, `uv.lock` or any script).

## 1. Method

- Closures were resolved in throwaway uv projects under `<scratchpad>/p4-02-k7q3/` (`requires-python = ">=3.11"`, universal lock, so every Python marker from 3.11 to 3.15 is in the result): (a) `websockets`, `protobuf`, `aiortc`; (b) `pipecat-ai==1.14.0` (core, no extras); (c) `pipecat-ai[websocket,runner,webrtc]==1.14.0`. The repository lock was not touched.
- Wheels and sdists were downloaded from PyPI into new directories under `<scratchpad>/p4-02-k7q3/dl*` and the SHA-256 of every file was compared with the digest in the PyPI JSON (all matched). Nothing downloaded was imported or executed. Only `METADATA`, licence, notice and SBOM files were unpacked and read. The shared libraries inside the `av`, `pylibsrtp`, `cryptography` and `soundfile` wheels were scanned as raw bytes for text strings (build configuration, licence strings, OpenSSL version); they were never loaded.
- Platform coverage of the reads: `av` 17.1.0 on macOS x86_64 and arm64, Linux manylinux x86_64 and aarch64, musllinux x86_64, Windows amd64 and arm64, plus the free-threaded 3.14t macOS arm64 and Linux x86_64 wheels, plus the sdist. `cryptography`, `pylibsrtp`, `google-crc32c`, `cffi`, `websockets`, `protobuf`: macOS arm64, Linux x86_64 and Windows (plus musllinux for cryptography and pylibsrtp, and the free-threaded and CPython 3.15 wheels for cryptography), plus the sdists. Pure-Python wheels once.
- The repository classifier `classify_metadata` from `scripts/check_licenses.py` was run (loaded as data from a script outside the repository) on the METADATA of each wheel, with the allowlist of `docs/security/license-policy.toml`. Its verdicts are quoted below.
- Public texts fetched: PyAV `scripts/ffmpeg-latest.json` at tag `v17.1.0`; pyav-ffmpeg tag `8.1.1-2` files `patches/ffmpeg.patch`, `scripts/build-ffmpeg.py`, and (main branch) `README.md`; x264 and x265 `COPYING`. Hashes of the fetched files: `patches/ffmpeg.patch` at `8.1.1-2` is `c6fff328c0c64ea5a3cb4b9bf44299553cb5897ab7bc5e22b4e16902c191d492`; `scripts/build-ffmpeg.py` at `8.1.1-2` is `35aed6c5b9a99af6eccd948d350f40bc9cc4087efca5535dc56d480bd55de048`.
- `uv run --locked python scripts/check_licenses.py` on the current tree (unchanged): `62 ok, 0 failed`.

## 2. websockets and protobuf (direct dependencies of tracer-evalsim)

Update 2026-10-27 (lead decision E-b, decision log "ADR 0026 AMENDED"): `protobuf` is not used: dropped 2026-10-27 (E-b), because the `pipecat-protobuf/1` wire format is encoded and decoded by hand (ADR 0026 rules 1 and 3). The review of protobuf 7.36.2 below is kept as history; `websockets` is the only direct runtime dependency of `tracer-evalsim` besides the workspace packages, and `aiortc` is the `[webrtc]` extra.

| Package | Version read | Licence as declared | Checker verdict | Licence file read | SHA-256 of the file | Verdict |
|---|---|---|---|---|---|---|
| websockets | 17.3 (PyPI latest, 2026-10-19; Requires-Python >=3.11) | `License-Expression: BSD-3-Clause` | allowed | `licenses/LICENSE`, BSD 3-clause "Copyright (c) Aymeric Augustin and contributors" | `3d6a0c050d8bec52fabad502e45fb25bd02bcadbd70dea34d447b6a0ff4e6da8` (the Windows wheel differs only by CRLF line ends: `0f445149...`) | permissive, go. No runtime dependencies (Requires-Dist empty) |
| protobuf | 7.36.2 (PyPI latest, 2026-09-17; Requires-Python >=3.10) | `License: 3-Clause BSD License` (free text, no expression, no classifier) | UNCLEAR ("needs an override") | `protobuf-7.36.2.dist-info/LICENSE`, BSD 3-clause "Copyright 2008 Google Inc." | `6e5e117324afd944dcf67f36cf329843bc1a92229a8cd9bb573d7a83130fea7d` (identical in every wheel and the sdist) | permissive, go WITH an `[[overrides]]` entry (section 6). Pipecat's own core resolution uses protobuf 6.33.6 (section 8; its licence file was not read). Not used: dropped 2026-10-27 (E-b); this review is kept as history, and section 6 no longer lists protobuf |

- The protobuf wheel is `abi3` (`cp310-abi3`) for macOS universal2, Linux x86_64, aarch64, s390x and Windows, plus a pure-Python wheel; no licence text for bundled C code beyond the one file was found (UNVERIFIED that the upb C extension has no further third-party notice).
- Lower bounds of both ranges are NOT read: only the versions above were. The lower-bound leg (D3) must re-read the licence of the lowest-direct version (see section 10).
- The bound for `protobuf` must stay consistent with Pipecat's own core requirement `protobuf<7,>=5.29.6` only for the interop wire format; this is a compatibility matter, not a licence matter, and `tools/samplebot` has its own lock.

## 3. aiortc closure (extra `[webrtc]` of tracer-evalsim)

Resolved set (universal lock, 15 packages with the three direct requirements; `pyee` has two versions by marker):

`aiortc 1.15.0` requires `aioice>=0.10.2,<1`, `av>=14,<18`, `cryptography>=44`, `google-crc32c>=1.1`, `pyee>=13`, `pylibsrtp>=0.10`, `pyopenssl>=25`. Transitive: `aioice` -> `dnspython`, `ifaddr`; `cryptography` -> `cffi` -> `pycparser`; `pylibsrtp` -> `cffi`; `pyee` -> `typing-extensions`; `pyopenssl` -> `cryptography`, and `typing-extensions` below Python 3.13.

| Package | Version | Declared | Checker verdict | Licence file (in wheel) | SHA-256 | Verdict |
|---|---|---|---|---|---|---|
| aiortc | 1.15.0 | `License-Expression: BSD-3-Clause` | allowed | `licenses/LICENSE` ("Copyright (c) Jeremy Laine", All rights reserved) | `bec75b5a59bbf89e979e4cb764775eb7b5d0167c5e907139e6f9953698c811b6` | permissive, go |
| aioice | 0.10.2 | `BSD-3-Clause` | allowed | `licenses/LICENSE` | `0569dfb61e866ffac17db4032d89b2e0285894e3d9b482b200aa26c0132dc284` | permissive, go |
| av (PyAV) | 17.1.0 | `BSD-3-Clause` | allowed | `licenses/LICENSE.txt` ("Copyright retained by original committers", BSD 3-clause text) | `76af0461ffb92e19f1c14449e95557d83a2dfaa1baf202d49e5f1d8746c0da19` (a Windows-wheel variant has a different hash; not compared byte by byte) | PyAV's own code permissive. The wheel BUNDLES an FFmpeg stack with GPL and LGPL parts the metadata does not mention: section 4. Conditional go (named exception) |
| cryptography | 50.0.2 | `Apache-2.0 OR BSD-3-Clause` | allowed | `licenses/LICENSE` (states "either" licence), `LICENSE.APACHE`, `LICENSE.BSD` | `3e0c7c09...ae6bb`, `aac73b3148f6...663fe`, `602c4c7482de...4fb` (full values in section 9) | permissive, go. The Rust extension reports OpenSSL 4.0.3 (string read from `_rust.abi3.so`, Apache-2.0 upstream; no OpenSSL notice file in the wheel) |
| cffi | 2.1.1 | `MIT-0` | allowed | `licenses/LICENSE` | `5ba24ddc57067f9249add644c3afc41a5d6dc37e23433ef759d95df370b0af63` | permissive, go |
| pycparser | 3.12 | `BSD-3-Clause` | allowed | `licenses/LICENSE` ("2008-2027, Eli Bendersky") | `0a9e78156ae8b96caf1b4f11ac2df424b83de29736f9e757630548a6272fdf90` | permissive, go |
| pylibsrtp | 1.0.0 | `BSD-3-Clause` | allowed | `licenses/LICENSE` ("Jeremy Laine") | `b771054910fe6a0ebf7e646e59f4190ad9a9379f17c75afceeb0d1818fe73740` (Windows CRLF variant `0254bf57...`) | permissive, go. `_binding.abi3.so` statically contains libsrtp2 and "OpenSSL 3.5.4" strings; no notice file for either in the wheel |
| google-crc32c | 1.9.0 | `License: Apache-2.0` | allowed | `licenses/LICENSE` (Apache-2.0 text) | `cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30` | permissive, go |
| pyopenssl | 26.4.0 | `License: Apache License, Version 2.0`, classifier Apache Software License | UNCLEAR (classifier only; free text) | `licenses/LICENSE` (Apache-2.0 text) | `cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30` (same as google-crc32c) | permissive, go WITH an `[[overrides]]` entry (section 6). Requires `cryptography<51,>=49` |
| pyee | 13.0.1 (Python <3.12) and 16.0.0 (Python >=3.12) | `License: MIT` + classifier MIT | allowed | `licenses/LICENSE` ("Copyright (c) 2021 Josh Holbrook") | `1ff556ca206b107f10e1c562ae454d3292e0296c3510eb90eb7f114caee32a66` (both versions) | permissive, go. The lock holds two versions of the same name |
| aioice dependency dnspython | 2.9.0 | `License-Expression: ISC` | allowed | NONE in the wheel or the sdist. Every source file carries the ISC permission text and the header "see LICENSE for text of ISC license" | n/a | permissive by the source headers and METADATA; finding L-3 (missing licence file) |
| aioice dependency ifaddr | 0.2.0 | `License: MIT` + classifier MIT | allowed | `LICENSE.txt` (MIT, in dist-info root) | `8700856576ae2bc80c63bc970250510d9213fb02fed006d5f22742c9ddde24d7` | permissive, go |
| typing-extensions | 4.16.0 | PSF-2.0 | allowed | already in the evalschema closure | - | go |

Closure facts: no package in this set declares or bundles an AGPL or SSPL licence. Packages with a compiled extension and platform-specific licence bytes: `av` (section 4), `pylibsrtp`, `cryptography`.

## 4. av / PyAV 17.1.0 and the bundled FFmpeg build (finding F9, verified)

What was read, from the files of the wheels (Linux x86_64 and aarch64, musllinux, macOS, Windows, 3.14t):

1. Metadata and licence file: `License-Expression: BSD-3-Clause`; the only licence files in `*.dist-info/licenses` are `LICENSE.txt` (PyAV, BSD 3-clause), `AUTHORS.py` and `AUTHORS.rst`. No licence, notice or source offer for FFmpeg or for any bundled codec library exists in any wheel (Linux wheels add `sboms/auditwheel.cdx.json`, which lists only `av`, `libxcb`, `libXau`, `libdrm` and on musl `libgcc`, `libstdc++`, `libbsd`, `libmd`, `libbz2`, `libxdmcp` with no licence fields).
2. Bundled libraries (file lists): FFmpeg `libavcodec` 62.28.101, `libavformat`, `libavfilter`, `libavutil` 60.26.101, `libavdevice` (Linux), `libswscale`, `libswresample`, plus `libx264` (so.165), `libx265` (so.216), `libmp3lame`, `libopencore-amrnb`, `libopencore-amrwb`, `libopus`, `libvpx`, `libwebp`, `libwebpmux`, `libsharpyuv`, `libdav1d`, `libSvtAv1Enc`, and on Linux `libgnutls`, `libnettle`, `libhogweed`, `libgmp`, `libunistring`, `libvpl`, `libasound`, `libxcb*`, `libdrm`, `libXau`. Windows adds `libiconv`, `zlib1`, libwinpthread, libstdc++ and libgcc runtimes. macOS has the same codec set as `.dylibs`.
3. Build configuration string read from `libavcodec`, `libavfilter`, `libavformat`, `libswscale` (Linux x86_64): `--enable-version3 --enable-gnutls --enable-libdav1d --enable-libmp3lame --enable-libopencore-amrnb --enable-libopencore-amrwb --enable-libopus --enable-libsvtav1 --enable-libvpx --enable-libwebp --enable-libxcb --enable-zlib --enable-libx264 --enable-libx265 --enable-nvenc --enable-nvdec --enable-amf --enable-libvpl ...`. There is no `--enable-gpl` and no `--enable-nonfree`. The libraries self-report `LGPL version 3 or later` (string read from `libavutil`, `libavcodec`, `libavformat`, `libavfilter`, `libswscale`; on every platform for `libavutil`).
4. The pinned build recipe: `PyAV` tag `v17.1.0` `scripts/ffmpeg-latest.json` points to pyav-ffmpeg release `8.1.1-2`. In that tag, `patches/ffmpeg.patch` edits FFmpeg's `configure` to move `libx264` and `libx265` OUT of `EXTERNAL_LIBRARY_GPL_LIST` and INTO `EXTERNAL_LIBRARY_VERSION3_LIST`, and `scripts/build-ffmpeg.py` enables `--enable-libx264 --enable-libx265` (skipped on 32-bit ARM) with `--enable-version3` and without `--enable-gpl`.
5. Upstream licences of those two libraries, from their public `COPYING` files: x264 is GNU GPL version 2 (`COPYING`, sha256 prefix `32b1062f7da84967`); x265 `COPYING` is GNU GPL version 2 and line 342 adds "This program is also available under a commercial proprietary license". Whether the binaries in the wheel are built under the GPL option or under a commercial licence is NOT stated anywhere in the wheel (UNVERIFIED). libmp3lame is LGPL, libgnutls, libnettle and libgmp are LGPL (or GPL dual) on the upstream projects: from general knowledge, not read from a file in the wheel (UNVERIFIED). `libopencore-amr*` is Apache-2.0 upstream (UNVERIFIED in the wheel).

Reading of the evidence (not legal advice): the `av` wheel metadata says BSD-3-Clause for the Python binding, while the same wheel redistributes dynamically linked GPL-2.0 libraries (x264, x265) and LGPL libraries (lame, gnutls, nettle, gmp, unistring) with no licence text and no source offer. FFmpeg's own build system treats libx264 and libx265 as GPL-only, and the pyav-ffmpeg patch removes that classification, so the self-reported "LGPL version 3 or later" does not describe the combined binary. This is an upstream compliance problem of the wheel. It does not change what tracer ships: tracer never vendors, copies or republishes `av`; it would appear only as a transitive dependency of the optional `[webrtc]` extra, installed by the user from PyPI.

The repository checker cannot see this: it classifies by metadata, and `av` classifies as `allowed` (BSD-3-Clause). So `test_no_lgpl_or_gpl_in_lock` will stay green with `av` in `uv.lock` and no `[[exceptions]]` entry can be attached to it (the checker consults exceptions only for non-allowed packages). The review is therefore the only record. Status: NAMED EXCEPTION (proposed, to be logged by the lead under the owner's rule "do not dwell on copyleft", 2026-10-23; the log entry is not written by this review) with the conditions of section 7.

Alternative that removes the bundled stack without dropping WebRTC: install `av` from the sdist against a system FFmpeg of the user's choice (`pip install av --no-binary av`, needs a compiler and a matching FFmpeg major). Not tested here. The option to drop the `[webrtc]` extra and move the WebRTC dialect to the backlog remains open; it is the owner's call, not a licence blocker.

## 5. Platform and Python availability (3.11 to 3.15)

- Universal lock of `websockets`, `protobuf`, `aiortc`: `pyee 13.0.1` for Python <3.12 and `pyee 16.0.0` for >=3.12 (16.0.0 requires Python >=3.12); everything else is one version. `typing-extensions` is required by `pyee` (always) and by `pyopenssl` below 3.13; `cryptography` needs it only below 3.11 (not applicable).
- Wheels: `av` 17.1.0 has `cp311-abi3` wheels (covers 3.11 to 3.15) for macOS x86_64 (11.0) and arm64 (14.0), Linux manylinux_2_28 x86_64, aarch64, musllinux x86_64 and aarch64, Windows amd64 and arm64, plus armv7l and `cp314t` free-threaded wheels. `cryptography` 50.0.2 has `cp311-abi3` and a `cp315-abi3.abi3t` wheel. `google-crc32c` 1.9.0 has `cp311` to `cp315` wheels for macOS, manylinux2014 and Windows but NO musllinux wheel.
- `uv pip compile --no-build` per target: all of 3.11 to 3.15 resolve to wheels on macOS x86_64 and arm64 (except one cross-resolution anomaly, below), manylinux x86_64 and aarch64, and Windows amd64. Findings:
  - musllinux (Alpine): CPython 3.12 to 3.15 do NOT resolve with `--no-build` because `google-crc32c` 1.9.0 has no musllinux wheel (3.11 falls back to `google-crc32c 1.5.0`). A source build needs a C compiler and the crc32c C library. Only the optional extra is affected.
  - `uv pip compile --python-version 3.15 --python-platform aarch64-apple-darwin --no-build` reports "no usable wheels" for `av`, while `3.14` resolves and a real CPython 3.15.0rc2 macOS arm64 environment (`uv pip install --dry-run --no-build`) resolves and would install all 15 packages (`pyee==16.0.0`). The cause of the cross-resolver difference was not isolated (UNVERIFIED); the real-interpreter result is the one that counts for this machine.
- No source build was run. `no-build-package` in the root `pyproject.toml` is for the dev (P4-01) to extend if the packages enter the root lock.

## 6. Proposed policy entries (text; the patch file holds the same, nothing applied)

Format read from `docs/security/license-policy.toml` and `scripts/check_licenses.py`: `[runtime.<key>]` with `closure` (normalised names, workspace members excluded) and `requires_dist` (exact `Requires-Dist` of the built wheel); `[[overrides]]` and `[[exceptions]]` pinned to name, version, licence file path inside the dist-info and its SHA-256; `[[rejected]]` with `name`, `spdx`, `reason`.

```toml
[runtime.evalsim]
# Runtime closure of tracer-evalsim (docs/security/P4-02-licenses.md). Workspace members excluded.
# websockets (BSD-3-Clause) on top of the evalschema closure; protobuf dropped 2026-10-27 (E-b: the
# protobuf dialect is hand-written, ADR 0026 rule 3). The [webrtc] extra is
# listed in [runtime.evalsim.extras_webrtc]. Any change must be a conscious edit.
closure = [
    "annotated-types",
    "pydantic",
    "pydantic-core",
    "rfc8785",
    "typing-extensions",
    "typing-inspection",
    "websockets",
]
# PROPOSED bounds; P4-01 fixes them (lower bound tested, D3) and re-reads the licence at the lowest
# version. aiortc is the extra.
requires_dist = [
    "tracer-evalschema==0.0.1",
    "tracer-evalcore==0.0.1",
    "websockets>=15.0,<18",
    "aiortc>=1.15.0,<2; extra == 'webrtc'",
]

[runtime.evalsim.extras_webrtc]
# Packages added by the [webrtc] extra beyond [runtime.evalsim]. typing-extensions is already in the
# base closure. av bundles an FFmpeg stack with GPL and LGPL parts (see the review, section 4).
closure = [
    "aioice",
    "aiortc",
    "av",
    "cffi",
    "cryptography",
    "dnspython",
    "google-crc32c",
    "ifaddr",
    "pycparser",
    "pyee",
    "pylibsrtp",
    "pyopenssl",
]

[[rejected]]
name = "pipecat-ai"
spdx = "BSD-2-Clause"
reason = "Not a rejection of its own licence. As a runtime dependency its core closure pulls num2words (LGPL), soxr 1.0.0 (LGPL-2.1-or-later), soundfile wheels that bundle libsndfile (LGPL-2.1), numba, onnxruntime and weekly releases; it would break test_no_lgpl_or_gpl_in_lock and the frozen closures, and its sarvam extra pins sarvamai==0.1.28 (rejected). Pipecat lives only in tools/samplebot with its own lock, pinned exactly (1.14.0), never in a tracer wheel and never in uv.lock. See docs/security/P4-02-licenses.md."

[[overrides]]
name = "pyopenssl"
version = "26.4.0"
spdx = "Apache-2.0"
scope = "runtime"
license_file = "licenses/LICENSE"
license_file_sha256 = "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30"
reason = "License field is the free text 'Apache License, Version 2.0' with only the Apache Software License classifier; LICENSE is the Apache-2.0 text (same bytes as google-crc32c). Optional extra [webrtc]."
```

Notes for the dev (checker work is outside this task and was not done):

1. `scripts/check_licenses.py` knows only three Phase 3 packages (`PHASE3_PROJECTS`); `tracer-evalsim` must be added with its policy key. `phase3_findings` fails any non-workspace requirement that has an `extra` marker ("extra dependency ..."), so the `[webrtc]` extra needs the same treatment as `[postgres]` for evaldb (a separate extras list and a `Provides-Extra` check). It also requires every non-workspace requirement to match `>=X,<Y`: `websockets` and `aiortc>=1.15.0,<2` fit (`protobuf` is dropped, E-b).
2. The policy tables above use the sub-table form requested by the task. The evaldb precedent is a list key (`extras_postgres = [...]` inside `[runtime.evaldb]`); either form needs loader support.
3. The overrides are needed only once the packages enter `uv.lock` (as with `pg8000`). Versions in overrides are pinned: a bump of pyOpenSSL re-triggers review (the protobuf override is withdrawn with the package, E-b).
4. When `tracer-evalsim[webrtc]` is added to a root dependency group, `uv.lock` will hold 12 more packages including two `pyee` versions; the closure test must tolerate one name with two versions.
5. The proposed `[[bundled_notices]]` and `[tools.samplebot]` tables in the patch file are documentary: the current loader ignores unknown tables. `test_samplebot_closure_reviewed_and_tool_only` and `test_license_policy_lists_phase4_closures` need the dev to decide how they are read.

## 7. Copyleft items and their status

| Item | Where | Licence read from | Status | Re-check when |
|---|---|---|---|---|
| FFmpeg stack bundled in `av` 17.1.0: libx264 and libx265 (GPL-2.0 per upstream COPYING), libmp3lame, gnutls, nettle, gmp, unistring (LGPL per upstream, not in wheel) | optional `[webrtc]` extra of tracer-evalsim, installed by the user from PyPI | build script and patch of pyav-ffmpeg `8.1.1-2`, configuration strings in the binaries, upstream COPYING | NAMED EXCEPTION (proposed under the owner's rule; the lead must log it). Conditions: (1) extra only, never in the base `Requires-Dist`; (2) no `av` file vendored, copied or republished in a tracer wheel or sdist; (3) README and the concept page state that the extra installs a third-party wheel with GPL/LGPL components and name the sdist alternative; (4) tracer uses audio only, no video codec is called; (5) the checker cannot see it, so this document and the `[[bundled_notices]]` entry are the record | any bump of `av` or of the `aiortc` bound on `av`; a new pyav-ffmpeg tag; the wheel gaining licence texts or dropping x264/x265; a new FFmpeg configuration string; any change of the owner's rule |
| certifi 2026.10.23 (MPL-2.0) | already in the repository (httpx/httpcore) | `licenses/LICENSE` | existing named exception (`[[exceptions]]`), unchanged | any certifi bump |
| num2words 0.5.14 (`License: LGPL`; `COPYING` is LGPL 2.1, sha256 prefix `1457a03d928aeb06`) | `tools/samplebot` closure only (Pipecat core dependency) | `COPYING` in wheel and sdist | TOOL-ONLY, never in a tracer wheel or `uv.lock`; no exception needed because it is outside the checked lock. If it ever enters the root lock the checker fails (correct) | any Pipecat bump (the dependency may change) |
| soxr 1.0.0 (`LGPL-2.1-or-later`; bundles libsoxr, `COPYING.LGPL`, `LICENSE-libsoxr.txt`, PFFFT) | `tools/samplebot` closure only (Pipecat core dependency `soxr~=1.0.0`). NOT named in F3 of the scope: new finding | `licenses/COPYING.LGPL`, `LICENSE-libsoxr.txt`, `LICENSE.txt` | TOOL-ONLY, as above | any Pipecat bump |
| soundfile 0.13.1 wheels bundle libsndfile (`_soundfile_data/COPYING` is LGPL 2.1, sha256 prefix `ad01ea5cd2755f60`, in the macOS, Linux and Windows wheels); the wheel metadata says BSD-3-Clause (classifier and short License field only) | `tools/samplebot` closure; the `tools/capture` pyproject also lists `soundfile` | `_soundfile_data/COPYING` | TOOL-ONLY, as above | any soundfile bump; any plan to use soundfile in a tracer package (it must not) |
| opencv-python-headless 4.14.0.94 (Pipecat `webrtc` extra; declared Apache-2.0) bundles FFmpeg: `cv2/LICENSE-3RD-PARTY.txt` states "FFmpeg is redistributed within all opencv-python packages" and holds LGPL 2.1 text (sha256 `36bae33968d5e67f66906bc595949cb78d65978604e7392f552a79006c93cf2c`); the macOS wheel also ships libx264 and libx265 dylibs | `tools/samplebot` only if the `webrtc` extra is used (P4-26 SmallWebRTC) | the wheel files above | TOOL-ONLY, as above. Not full-reviewed (only the notice headings were read) | the samplebot extras are chosen |
| libsndfile/mpg123/lame inside soundfile, the Intel MKL entry and MPL-2.0 texts in onnxruntime `ThirdPartyNotices.txt` | tool-only | the notices file (338 KB, grep only) | TOOL-ONLY; not read line by line (UNVERIFIED) | onnxruntime bump |
| psycopg, psycopg-binary (LGPL-3.0-only) | already `[[rejected]]` | - | dropped, unchanged | - |

Dropped (not added): `pipecat-ai` as a runtime dependency; `sarvamai` (already rejected, and the Pipecat `sarvam` extra pins `sarvamai==0.1.28`, PyPI `license` and `license_expression` null, no classifier, no licence file; requires `websockets>=12.0`).

## 8. tools/samplebot closure (TOOL-ONLY)

Pin policy: the scope pins `pipecat-ai==1.14.0` exactly in `tools/samplebot`, as `tools/capture` pins `pipecat-ai[...]==1.13.0`. PyPI on 2026-10-27: latest is 1.14.0, uploaded 2026-10-24 (`License-Expression: BSD-2-Clause`, Requires-Python >=3.11); there were 8 releases from 2026-08-26 (1.8.0) to 2026-10-24 (1.14.0), about one per week. Every bump re-resolves the closure and re-triggers this review. The lock lives in `tools/samplebot/uv.lock`, outside the workspace and outside the root `uv.lock`; the checker never sees it.

Core closure of `pipecat-ai==1.14.0` (no extras), universal lock, 53 packages (CPython 3.14.7 host; names and versions below are those locked):

| Package | Version | Declared (PyPI JSON) | Checker verdict on the wheel METADATA | Licence file read | Note |
|---|---|---|---|---|---|
| pipecat-ai | 1.14.0 | BSD-2-Clause | allowed | `licenses/LICENSE` "BSD 2-Clause License, Copyright (c) 2024-2026, Daily" (sha prefix `e31a733ecb79cbff`); plus `LICENSE-shadcn.md` (template) | The wheel also contains `silero_vad.onnx` and `smart-turn-v3.2-cpu.onnx`; the licences of those two model files are not stated in the wheel (UNVERIFIED) |
| num2words | 0.5.14 | `LGPL` | copyleft | `COPYING` (LGPL 2.1) | section 7. Requires `docopt` (MIT) |
| soxr | 1.0.0 | `LGPL-2.1-or-later` | copyleft | `COPYING.LGPL`, `LICENSE-libsoxr.txt`, `LICENSE.txt` | section 7 |
| soundfile | 0.13.1 | `BSD 3-Clause License` | unclear (classifier only) | `LICENSE` (BSD 3) and `_soundfile_data/COPYING` (LGPL 2.1, libsndfile) | section 7 |
| onnxruntime | 1.31.1 | `MIT License` | unclear (classifier only) | `onnxruntime/LICENSE` (MIT, sha prefix `2f07c72751aed997`), `ThirdPartyNotices.txt` | notices list Intel MKL, Mbed TLS (Apache-2.0 OR GPL), MPL-2.0 text and others; not reviewed in full |
| numba | 0.68.0 | `BSD` | unclear (bare word "BSD") | `licenses/LICENSE` (BSD 2, sha prefix `3a70e0a21cad0314`), `LICENSES.third-party` | vendored libraries listed in the second file; not reviewed in full |
| llvmlite | 0.50.0 | `BSD-2-Clause AND Apache-2.0 WITH LLVM-exception` | unclear (the checker does not know the `WITH` exception) | `LICENSE`, `LICENSE.thirdparty` | permissive |
| sentencex | 1.0.32 | `MIT` | allowed | wheels carry no licence file; the sdist has `LICENSE` (MIT, sha prefix `d6d192c000d71eb9`); the wheel SBOM lists 37 Rust crates, all MIT, Apache-2.0, Unlicense, Unicode-3.0 or combinations | permissive |
| loudness | 0.2.0 | `MIT` | allowed | `licenses/LICENSE` (MIT); sdist vendors libebur128 (MIT), gcem (Apache-2.0), thread-pool (MIT) | permissive |
| resampy | 0.4.3 | `ISC` | allowed | `LICENSE.md` (ISC) | permissive |
| openai | 3.39.0 | `Apache-2.0` | allowed | `licenses/LICENSE`; `_vendor/httpx_aiohttp/LICENSE` (BSD-style) | requires `httpx2` 2.16.0 (BSD-3-Clause) |
| loguru | 0.7.3 | classifier MIT only | unclear (classifier only) | none in the wheel (UNVERIFIED file) | permissive by classifier |
| audioop-lts | 0.2.2 (Python >=3.13) | `PSF-2.0` | allowed | `licenses/LICENSE` | permissive |
| numpy 2.4.6 / 2.5.4, pillow 13.0.0, pydantic 2.14.1, pydantic-core 2.50.1, aiohttp 3.14.5, protobuf 6.33.6, and 30 more | - | PyPI JSON read: all permissive (MIT, BSD, Apache-2.0, PSF-2.0, ISC, MIT-CMU, MIT-0, 0BSD, combinations of these) | not run on every wheel | not read from files for these | UNVERIFIED beyond PyPI metadata |

Optional Pipecat extras the samplebot may need (`websocket`, `runner`, `webrtc`; resolved, 111 packages in all): declared licences read from PyPI JSON are permissive, with one weak copyleft `certifi` (MPL-2.0) and `opencv-python-headless` (section 7); wheel files were read only for `opencv-python-headless` and the `aiortc` set. The remaining 50 packages were not read from files (UNVERIFIED).

Conclusion for the samplebot: the closure contains three LGPL items in the core closure (num2words, soxr, libsndfile inside soundfile) plus FFmpeg-bearing wheels in the extras. All of it is acceptable as TOOL-ONLY under the owner's rule because: it is outside every tracer wheel and sdist, outside `uv.lock`, never imported by tracer code, and run only by the owner or CI as a separate process. `test_samplebot_closure_reviewed_and_tool_only` should assert exactly that, not that the closure is permissive.

## 9. Licence file hashes (full SHA-256 where not given above)

| Package | File | SHA-256 |
|---|---|---|
| cryptography 50.0.2 | `licenses/LICENSE` | `3e0c7c091a948b82533ba98fd7cbb40432d6f1a9acbf85f5922d2f99a93ae6bb` |
| cryptography 50.0.2 | `licenses/LICENSE.APACHE` | `aac73b3148f6d1d7111dbca32099f68d26c644c6813ae1e4f05f6579aa2663fe` |
| cryptography 50.0.2 | `licenses/LICENSE.BSD` | `602c4c7482de6479dd2e9793cda275e5e63d773dacd1eca689232ab7008fb4fb` |

All other full hashes are in the tables above. The `aioice`, `aiortc`, `cffi`, `cryptography`, `google-crc32c`, `protobuf`, `pycparser`, `pyee`, `pyopenssl` licence files are byte-identical in every platform wheel and in the sdist read. The Windows wheels of `pylibsrtp` and `websockets` differ from the other platforms only by CRLF line ends (checked by comparing with line ends normalised). The Windows `av` wheels carry a `LICENSE.txt` with a different hash; that difference was not compared.

## 10. Not verified

1. Lower bounds: the licence and wheel availability of the lowest-direct versions of `websockets`, `protobuf`, `aiortc` (and so `av>=14`, `cryptography>=44`) were not read. The bounds in section 6 are proposals. (Update 2026-10-27, E-b: `protobuf` is dropped, so its lower bound no longer matters.)
2. The licence of the binaries actually shipped inside the `av` wheel for libx264 and libx265 (GPL option or commercial), and the licence of lame, gnutls, nettle, gmp, unistring and opencore-amr as built: not stated in the wheel; upstream texts only for x264 and x265. The `av` Linux armv7l, ppc64le, riscv64 and 32-bit wheels were not downloaded.
3. Whether pyav-ffmpeg `8.1.1-2` is the build in the `av` 17.1.0 wheels: inferred from PyAV's `v17.1.0` tag (`ffmpeg-latest.json`); the wheel's configuration string is consistent with that recipe.
4. The sdist of `av`, `protobuf`, `pylibsrtp` and others were read for licence files only; no source build, no import of any downloaded code.
5. Samplebot: licences of 40 or so non-named packages in the core closure and 50 in the extras read from PyPI JSON only; `onnxruntime` and `numba` third-party notices not read line by line; `silero_vad.onnx` and the smart-turn model licences; `loguru` has no licence file in the wheel.
6. Whether `tracer-evalsim` also depends on `httpx` or `tracer-evalproviders` (the scope table lists none): the proposed closure assumes it does not. If it does, add the httpx closure and the certifi note.
7. No vulnerability scan was run (outside P4-02; the security audit P4-32 covers advisories). No source comparison with Dograh or VoicEra was done for this task.
8. Linux/Windows/macOS behaviour of the optional extra beyond the resolution results in section 5 was not installed or run.

## 11. Findings and gate

| ID | Severity | Item | Fix |
|---|---|---|---|
| L-1 | P1 (owner decision, not a code blocker) | `av` 17.1.0 wheels bundle GPL-2.0 libx264/libx265 and LGPL libraries behind a BSD-3-Clause metadata field, built with a patch that moves x264/x265 out of FFmpeg's GPL list; no licence texts shipped; invisible to `check_licenses` | Log the named exception with the five conditions of section 7, or drop the `[webrtc]` extra to the backlog; document the sdist alternative |
| L-2 | P2 | Pipecat's core closure also holds `soxr` 1.0.0 (LGPL-2.1-or-later), which F3 of the scope does not name | Tool-only; correct F3 and the `[[rejected]]` reason (done in the proposal) |
| L-3 | P3 | `dnspython` 2.9.0 ships no LICENSE file (headers refer to one) | None required; the ISC text is in each source file; note in the NOTICE review |
| L-4 | P3 | `protobuf` 7.36.2 and `pyopenssl` 26.4.0 need `[[overrides]]` (free-text licences) when they enter `uv.lock` | Add the two overrides of section 6. Update 2026-10-27 (E-b): protobuf is not used, so only the pyopenssl override remains |
| L-5 | P3 | The checker has no support for a Phase 4 package with an extra, and its Phase 3 loop rejects any extra | Dev work in P4-01/P4-02 follow-up (section 6 notes) |
| L-6 | P3 | `pylibsrtp` and `cryptography` wheels statically contain libsrtp2 and OpenSSL with no notice file | Permissive upstream; no action beyond the NOTICE review |

Gate recommendation: NON-BLOCKER for P4-02 closing and for adding `websockets` and `protobuf` (permissive, go, with the protobuf override; update 2026-10-27, E-b: `protobuf` is not used, so only `websockets` is added); the `aiortc` closure is a go only as an optional extra and only together with the owner/lead decision on L-1 (named exception under the owner's rule). `pipecat-ai` stays out of every tracer wheel and out of `uv.lock`; its closure is recorded tool-only. Re-run `check_licenses` after the dev adds the policy entries.
