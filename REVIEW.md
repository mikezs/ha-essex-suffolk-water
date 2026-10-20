# HACS follow-up review — 20 October 2026

Review trigger: [hacs/default#11239](https://github.com/hacs/default/pull/11239),
Frenck's request for private portal sessions.

## Findings addressed

| Finding | Resolution |
| --- | --- |
| Shared HA session carried supplier login cookies | Each entry uses `async_create_clientsession(hass)` and its own cookie jar. Credential checks use another, short-lived session. |
| Cleanup needed to cover failed setup and credential checks | Entry unload/failed setup detach the private session; config-flow `finally` detaches on every exit, including cancellation. HA's connector remains managed by HA. |
| Review's suggested `session.close()` is blocked by HA | Use `session.detach()`, as documented by HA's session factory. Real-session regression tests verify the session becomes closed without closing the shared session. |
| Advertised HA 2025.2 minimum lacked required statistics APIs | Corrected minimum to HA 2025.11 and tested it. |
| Expired portal profile was not renewed before account discovery | Library account/meter discovery now uses the existing token/profile renewal path. |
| Failed login/token acquisition could leave stale authenticated state | Library clears old auth before login and only marks success after the JWT is available. Coordinator retries authentication after transient errors. |
| Malformed JSON/rows/tokens could escape as untyped exceptions | Library maps these to typed errors; missing account envelopes cannot silently masquerade as zero accounts. |
| Invalid numeric usage became zero or non-finite statistics | Library rejects malformed/non-finite consumption and cost; genuine zero and absent cost remain valid. |
| Generic API failures were skipped as empty history days | Only JSON-null/empty usage is treated as empty. Other errors stop backfill and retry on the next successful poll. |
| Failed backfill enabled newer statistics and advanced past missing history | Incremental writes remain gated until backfill succeeds; failed backfill is rescheduled. |
| Duplicate timestamps could overcount or raise `fold must be either 0 or 1` | Ordinary hours are deduplicated; only the autumn repeated hour permits a second occurrence. Live totals and imported statistics use the same mapping. |
| Errors included raw response bodies/account payloads | Library errors now omit those payloads. |
| Release workflows referenced a missing `RELEASE_TOKEN` secret | Release from locally committed version tags, using the built-in GitHub token and PyPI trusted publishing. Tagged code is validated before publishing. |

Reviewed the integration setup/unload, flows/reauth, coordinator/backfill,
statistics and DST handling, entities/sensors, diagnostics, manifest/HACS metadata,
CI/release configuration, and the library transport/authentication/models/CLI.

## Verification

- Library: 90 tests pass, 100% branch/statement coverage; Ruff, strict mypy,
  wheel/sdist build and Twine checks pass.
- Integration: 31 tests pass with 98.71% branch/statement coverage on HA
  **2025.11.0**, **2026.2.3**, and **2026.10.2**. Ruff, mypy and Bandit pass.
- Local HTTP portal tests exercise actual requests, cookie propagation, session
  isolation and malformed-JSON handling without mocking `session.request`.
- HA tests verify private cookie jars across entries and HA, cleanup after unload
  and failed setup, and credential-check cleanup on success/failure.
- A bounded live check ran the updated coordinator and local library on this Mac
  against the supplier: one meter, usage and cost returned; latest reading
  **2026-10-19 17:00 Europe/London**. Credentials stayed in process memory.
  Production integration files and recorder statistics were not changed by this check.

## Remaining functional limitations

- Statistics are append-only: supplier revisions to already imported provisional
  readings are not reconciled. Missing usage/cost that arrives *behind* a later
  imported timestamp also needs an explicit reconciliation feature.
- API timestamps do not include UTC offsets. Autumn-fold coverage uses synthetic
  fixtures; actual supplier spring/autumn boundary payloads still need capture
  and comparison before claiming complete real-world DST validation.
- Sensors are created for meters discovered at setup. Reload after a supplier
  meter replacement/new meter to create its entities.
- The live check covered recent usage, not a full multi-year backfill or a live
  recorder migration. Existing statistics were intentionally preserved.
