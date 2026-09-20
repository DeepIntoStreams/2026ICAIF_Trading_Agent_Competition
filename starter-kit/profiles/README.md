Profiles must contain the actual organizer-issued deployment information. This production package includes only the organizer-issued Competition 99 profile. `competition.json` publishes the official calendar and service links; automatic transport uses the selected profile.

Required JSON fields:

- `base_url`: public Codabench HTTPS origin.
- `competition_id`: positive integer for this original-workflow clone.
- `phases`: `{ "unified": 120 }` for this competition's one native phase. Legacy profiles may map `registration`, `validation`, `official`, `final_submission` to four distinct IDs; all four keys may also share one ID. The client normalizes unified profiles into four logical lookup keys.
- `backend_base_url` (optional): published standalone backend HTTPS base. Omit to use the authenticated competition gateway at `/extensions/icaif2026/<competition_id>/backend`.
- `tasks` (optional): one-element array with the organizer-issued native trading task ID.

Never place tokens in a profile. Use the organizer's explicit profile, credentials and checkpoints; do not copy IDs from another competition.

The included `profile99-production.json` targets official Competition 99. Registration, Validation, Official and Final all use native Phase 120; the backend routes each original filename to its logical stage.

Optional `label` is descriptive text; `test_mode` is an explicit boolean test notice. These fields do not alter the server clock or scoring rules.
