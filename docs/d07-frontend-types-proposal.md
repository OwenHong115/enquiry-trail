# D07 frontend types: decision proposal for the operator

Status: proposal only, dated 2026-11-02. This document does not
change D07. D07 is NOT changed by this doc, and no code changes
follow from it until the operator picks an option.

## Current state

`frontend/src/lib/types.ts` is hand-aligned to the backend
OpenAPI/Pydantic shapes. Its header says so: field names mirror
`backend/app/api/*.py` exactly, and generation was impractical in
the sandbox where it was written. D07 requires the frontend types
to be generated from the authority OpenAPI instead. Hand alignment
drifts silently: a renamed or re-typed backend field produces no
frontend build failure until a test or a user hits it.

## Option A: supply the full authority package, generate from it

Precondition: the operator supplies the full authority package.
It is currently missing from this repository. Missing files:
authority.md, openapi.yaml, vocabulary.yaml,
generate_vocabulary.py, and the contract fixtures. The repository
plan package is only the partial update package (PLAN.md,
contracts/tdd.yaml, contracts/verification.yaml,
docs/build-layout.md, CHANGELOG.md, plus APPLY-STATUS.txt).

Steps, in order:

1. Vendor or reference the supplied authority `openapi.yaml` at a
   fixed path recorded in the repo; record its hash.
2. Pin the generator in the frontend lockfile
   (`frontend/package-lock.json`): add `openapi-typescript` as a
   devDependency at an exact version, installed with `npm ci`.
   No floating version, no global install.
3. Generate: `npx openapi-typescript <authority openapi.yaml> -o frontend/src/lib/api-types.ts`.
   The generated file is committed and carries a generated-file
   header; it is never hand-edited.
4. Reproducibility: regenerating from the same authority file with
   the pinned generator must produce a byte-identical output.
   A CI or local check regenerates and diffs; a non-empty diff
   fails the check.
5. UI-only types stay separate: view-model and UI-state types that
   do not exist in the API (form state, display helpers) remain in
   `frontend/src/lib/types.ts`, which then re-exports or wraps the
   generated API types instead of redeclaring them.
6. Enum and vocabulary values come from the same authority source,
   so C01-style consistency can be checked against them offline.

This keeps the authority direction D07 requires: the contract
package is the source, code is derived from it.

## Option B: amend D07 to generate from the FastAPI runtime OpenAPI

Steps: export the running app's OpenAPI (`app.openapi()`), pin
`openapi-typescript` in the frontend lockfile at an exact version,
generate `frontend/src/lib/api-types.ts` from that export with the
same regenerate-and-diff reproducibility check as Option A, and
keep UI-only types separate in `types.ts` as above.

This changes the authority direction explicitly: D07 would no
longer derive frontend types from the authority contract package.
The source becomes whatever the FastAPI/Pydantic code emits at
runtime. Risk: the runtime and the generated types could share a
Pydantic error. If a field type or enum is wrong in the Pydantic
model, the generated frontend type is wrong in exactly the same
way, and the type check passes while both sides disagree with the
authority contract. Generation stops being an independent check.

## Recommendation

Option A if the full authority package can be produced. It keeps
D07's authority direction and makes generation a real check.

Else Option B, only with an added enum-parity check: every enum
and vocabulary value in the generated types is compared against
the vocabularies in `contracts/verification.yaml` (the partial
update package already in this repository), and a mismatch fails
the check. That restores an independent source for the values most
likely to drift, even though field shapes still derive from the
runtime models.

## What this proposal does not do

- It does not change D07.
- It does not change `frontend/src/lib/types.ts` or any code.
- It records no acceptance verdict. Any verdict stays
  `PENDING OPERATOR`.
