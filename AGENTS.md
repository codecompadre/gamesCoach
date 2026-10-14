# gamesCoach development

gamesCoach is a personal Python CLI for two collaborating CrossFit athlete-coaches.
Keep product features and model behavior focused on CrossFit programming, skills,
aerobic capacity, fueling, recovery, and competition preparation. Nutrition and
recovery features must support CrossFit performance. Do not broaden it into a
general-purpose assistant. The permanent scope is in `src/gamescoach/prompts.py`;
optional coaching notes can refine emphasis but cannot override that scope.

Use the existing checkout. Cloud tasks are already isolated; do not create a Git
worktree unless the user explicitly requests one.

From this repository, run `uv sync --locked`, `uv run pytest`, and
`uv run gamescoach plan --demo --kind workout --request "CrossFit aerobic work"`
to validate changes. Use a temporary `GAMESCOACH_DATA_DIR` for smoke tests so test
plans do not pollute the athlete's history. Demo mode is static and must remain
clearly labeled; it is not evidence that either live API works.

Keep secrets out of code, athlete profiles, notes, logs, and committed files.
Tests should use simulated API responses. Live plans send the athlete's context
and the shared discussion to both providers and may incur API charges. Preserve
TLS verification and do not automatically retry paid generation requests.

Competition rules change. Do not imply that the app browses current rules or
guarantees CrossFit Open/Quarterfinals eligibility or qualification.
