# doctor-vm — fleet doctor preflight fixture (issue #19)

Stubbed `gh` / `loginctl` / `systemctl` on `PATH` plus a temp state dir: the
doctor must turn blast-radius *advice* into checks with named hard failures.

Scenario knobs (env vars the stubs read):

- `DOCTOR_STUB_SCOPES_FILE` — file whose contents land on gh's
  `Token scopes:` line (e.g. `'repo'` good; `'repo', 'admin:org'` over-scoped).
- `DOCTOR_STUB_PROTECTION` — `present` (default) or anything else for a 404.
- `DOCTOR_STUB_LINGER` — `yes` (default) or `no`.
- `DOCTOR_STUB_TIMER` — `active` (default) or `inactive`.

Cases:

1. **All-good** — every check passes, `fleet.py doctor` exits 0.
2. **Missing linger** — `DOCTOR_STUB_LINGER=no` → hard failure named
   `linger`, exit ≠ 0.
3. **Over-scoped token** — `DOCTOR_STUB_SCOPES_FILE` containing `admin:org`
   → hard failure named `token-scopes`, exit ≠ 0.

The doctor is read-only and stdlib; it shells `gh`, `loginctl`, `systemctl`
and `git`, which is exactly why the stubs work and why the checks are graded
mechanically instead of by the model.