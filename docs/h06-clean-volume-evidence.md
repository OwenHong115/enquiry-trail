# H06 clean-volume evidence

Record of a clean-volume stack start. It supports checklist item H06.
It does not sign off H06; H06 stays OPEN pending operator sign-off.

## Run identity

- Date: 2026-11-02
- Commit: `e04ef02`
- Compose project: `eqclean` (fresh volume)
- Command: `docker compose -p eqclean up -d --build`

## Observed

- Migrate and seed completed; seed created exactly 1 operator.
- API healthy.
- Web returned HTTP 200.
- Login returned HTTP 200.

## Teardown

- `docker compose -p eqclean down -v` run afterwards; the `eqclean`
  volume was removed.
- The main stack was restored afterwards.

## Status

Evidence recorded. H06 stays OPEN pending operator sign-off.
Connected mode remains UNVERIFIED (see `docs/connected-handover.md`);
A30 live smoke was not run (no real account).
