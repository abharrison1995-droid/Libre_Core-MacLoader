# Decision record: binding Apple Recovery to Sequoia 15.0 build 24A335

**Status:** Accepted. Recovery acquisition stays blocked.
**Date:** 2026-09-24
**Scope:** ThinkPad T480s 20L8, BIOS N22ET85W 1.62, frozen target macOS Sequoia 15.0 build `24A335`
**Supersedes:** nothing. This record extends the 2026-09-11 entry in [RESEARCH_LEDGER.md](RESEARCH_LEDGER.md).

## Question

Can MacLoader use an Apple-authoritative, authenticated method to prove that a Recovery product or payload is build `24A335` before it locks or downloads that product? If so, MacLoader should implement a fail-closed evidence-resolution path. If not, acquisition stays blocked, and this record names the external blocker.

## Constraints

- MacLoader must not infer a build from a product ID, file size, date, URL, or version pattern.
- MacLoader must not use plaintext HTTP, and it must not use a third-party mirror or a product-to-build table maintained by the community.
- The `15.0/24A335` target must not change without a separate user decision.

## Routes evaluated

| # | Route | Apple-authoritative | Authenticated transport or signature | Binds to a build | Result |
|---|---|---|---|---|---|
| 1 | `osrecovery.apple.com` `InstallationPayload/RecoveryImage`, using the pinned OpenCore 1.0.7 protocol | Yes | The pinned upstream client uses **HTTP**; the HTTPS request returned **HTTP 405** in the 2026-09-11 probe | **No** | Rejected |
| 2 | Apple-signed chunklist (`CNKL`, signature method 1, Apple EFI ROM RSA key) | Yes | Yes (RSA signature over chunk digests) | **No.** It authenticates image bytes, not a build | Kept as an integrity check only |
| 3 | Build string inside a chunklist-verified BaseSystem image (`SystemVersion.plist`) | Yes, if the bytes are Apple-signed | Yes, through route 2 | Yes, for that payload | Not reachable; see below |
| 4 | Apple Software Update catalog full installer (`InstallAssistant.pkg`, `.dist` build metadata) | Yes | HTTPS catalog and Apple-signed package | Yes, for a full installer | Different product, out of scope; needs a user decision |

### Route 1: Recovery discovery protocol

The pinned OpenCore `macrecovery.py` sends a board ID, an MLB, and an `os` value of `default` or `latest` to `http://osrecovery.apple.com/`. Apple returns only these keys: `AP` (product), `AU`/`AH`/`AT` (image URL, hash, token), and `CU`/`CH`/`CT` (chunklist URL, hash, token). The protocol has no field that returns a version or build, and it has no request parameter that selects one. The server returns the Recovery it currently serves for that board. It cannot be asked for a historical build. The 2026-09-11 query returned product `696-28424` without any build. MacLoader classifies that result as `AMBIGUOUS`, and `macloader/recovery/discovery.py` never returns `DISCOVERED`.

MacLoader's policy requires HTTPS. The HTTPS form of the exchange returned HTTP 405, so the protocol cannot be used even as an unbound query without dropping to plaintext. Plaintext is prohibited.

### Route 2: Signed chunklists

`verify_apple_chunklist` checks signature method 1 against Apple's EFI ROM public key and rejects the unsigned digest-only method 2. The signature proves that Apple produced the image bytes. It says nothing about which macOS build those bytes contain, so it cannot close the build question.

### Route 3: Build metadata inside the signed payload

In principle, a BaseSystem image whose chunklist verifies under Apple's key carries Apple-authored `ProductBuildVersion` metadata, so that signed payload would be bound to its build. Three problems block this route:

1. **No route delivers a 24A335 payload.** Route 1 serves only the current Recovery for a board over a transport MacLoader cannot use. No other Apple-authoritative source for a 24A335 Recovery image is known.
2. **The parser does not exist.** A dependency-free, bounded UDIF/APFS reader would be needed to read `SystemVersion.plist` from an untrusted image. None has been implemented or reviewed. Mounting on the preparation host is not acceptable either.
3. **A Recovery build does not choose the installed build.** Recovery's "Reinstall macOS" downloads its installer from Apple at install time. A 24A335 Recovery therefore does not prove that the installer will be 24A335. The runbook already requires cancelling when the offered build cannot be confirmed.

### Route 4: Full-installer catalog

Apple's Software Update catalog lists full `InstallAssistant.pkg` products with build metadata, served over HTTPS as Apple-signed packages. This would be an authoritative 24A335 artifact if Apple still lists that product. It is not a Recovery product, though. Using it changes the acquisition product, the verification boundary (X.509/CMS package signatures verified against Apple's roots), and the media layout. It also needs macOS-only tooling (`createinstallmedia`) that is unavailable on the Linux preparation host. Whether 15.0/24A335 is still listed was not checked in this pass: this session's network policy refuses CONNECT to Apple hosts (HTTP 403 from the egress proxy on 2026-09-24), so Apple could not be re-probed. Adopting route 4 is a scope change that needs a separate user decision. It is not implemented.

## Decision

1. **No authenticated route binds a Recovery product or payload to build 24A335.** MacLoader does not implement a build-resolution path. `macloader recovery download` stays unreachable, because discovery never yields `DISCOVERED`.
2. **Only current evidence is reported.** `macloader recovery resolve` (and the TUI's discovery action) records a redacted observation, including transport failures, in the owner-only private workspace (`private/recovery/discovery-evidence.json`). `macloader preflight` and `macloader recovery status` derive the `exact_recovery` check from that record:
   - `missing`: no record. Next action: run `macloader recovery resolve`.
   - `externally_blocked`: a current record from this policy and target that shows `AMBIGUOUS`, `UNAVAILABLE`, or `FAILED`, together with its product ID or diagnostic and timestamp.
   - `stale`: the record is older than 7 days, or it belongs to another policy or target.
   - `blocked`: the record is malformed, has an edited integrity digest, is future-dated, has unsafe permissions, or claims `DISCOVERED`, `LOCKED`, or `VERIFIED`. Discovery cannot produce those three states, so such a record is rejected as forged.
3. **Evidence never makes Recovery ready.** The record is local and unauthenticated: its integrity digest detects accidental edits, not deliberate forgery. The derived state is therefore never `ready`.
4. The target stays `15.0/24A335`, and plaintext discovery stays prohibited.

## What would unblock the gate

Any one of these requires fresh research and review before code changes:

- Apple offers an HTTPS discovery method that returns authenticated version/build metadata for a Recovery product, or accepts a build selector and signs its response.
- An Apple-authoritative HTTPS source for a 24A335 Recovery image, plus a reviewed bounded parser that reads the build from the chunklist-verified bytes. This must also resolve item 3 of route 3: what the installer downloads.
- A user decision to adopt route 4 (a full installer) with its different product, signature and host requirements, or to change the target.

## Verification

- `tests/unit/test_recovery_evidence.py` covers forged records (exact-state claims, both resealed and edited), corrupted and unexpected fields, unredacted diagnostics, stale, future-dated and mismatched-policy/target records, broad file permissions, and cancellation. It also checks that preflight reports current evidence instead of a fixed message.
- The existing discovery tests (`tests/unit/test_recovery_discovery.py`) still show that an `AP` product ID alone is `AMBIGUOUS`.
