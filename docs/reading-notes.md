# Reading notes — S1 (normalization and routing)

Binding rules I will be held to, in my own words:

- The contract files are the authority. If code and contract disagree,
  the contract wins; I fix the code or change the contract explicitly,
  never drift silently.
- Generated outputs are never hand-edited. Change the source, then
  regenerate and review the diff.
- An acceptance item is only passed after real execution. No run means
  not passed, however good the code looks.
- Layout and dependency direction come from the slice contract:
  api -> services -> (domain | ports | db). I follow it by discipline,
  not by tooling.
- Slices go Red -> Green -> Refactor, one at a time. This session is
  S1 and stops at Red: failing tests only, no behaviour.
- Domain code stays pure: stdlib plus vocabulary enums, public outputs
  only in assertions, no private call counts and no SQL text.
- The plan package stays outside this repo. Nothing from it is copied,
  vendored, staged or published; tests transcribe values by hand with
  a source comment instead of loading plan files at runtime.
- Project dependencies live in the uv-managed `backend/.venv`. The
  base interpreter is never used for project installs.

Genuine ambiguities and how I read them:

- The layout contract says domain uses only stdlib, while intake
  canonicalization names the phonenumbers library for AU numbers.
  I read the specific canonicalization clause as winning for the
  phone function only; everything else in domain stays stdlib.
  Domain rejection for an invalid nonempty phone is `ValueError`;
  the HTTP 422 mapping belongs to a later API slice, not S1.
- No other ambiguity in S1 forces a cross-layer guess: owner codes,
  review flags, reasons and the routing hash bytes are all spelled
  out in the contracts.
