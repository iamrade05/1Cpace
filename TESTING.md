# 1Cpace V8 Testing Baseline

## Purpose

The automated test suite is a merge gate. A required test failure blocks the change.

## Controlled baseline

Run tests from a clean checkout with the project's declared dependencies installed. CI supplies explicit test-only configuration so results do not depend on a developer's local `.env`.

The historical baseline of **14 failures out of 177 tests** was observed in a clean environment without `.env`, `ENCRYPTION_KEY`, and `TESSERACT_CMD`. It is an environment-specific observation, not a universal test count.

As of 2026-09-05 the suite is **195 passed, 0 failed** with the project venv and `ENCRYPTION_KEY` configured. The 11 long-standing failures were classified and closed: three implementation defects (webhook commit on an unbound connection, contract terms ignoring `terms_text`, the DebiCheck discount tier gated on an arrangement that cannot exist yet), one schema defect (`tenant_integrations` was never created), and seven stale tests.

The October 2026 release review supersedes that historical count. See
`RELEASE_REVIEW.md` and `artifacts/release-tests-final.xml` for current validation.
Test startup disables the developer `.env`, supplies a test encryption key, and
isolates database and upload defaults even for tests that build their own apps.
CI checks Python 3.12 (the beta runtime) and 3.13, plus desktop/mobile browser
checks on 3.12. CI results must still be observed after this change is pushed.

## Local command

`pytest` is intentionally absent from `requirements.txt` — production installs
no test tooling. Install the dev set instead:

```text
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

On Windows, pass `--basetemp` to a short path outside the project to avoid a
`PermissionError [WinError 5]` during pytest's temp cleanup.

Run `python -m scripts.verify_ui` for disposable browser checks. Install the
browser first with `python -m playwright install chromium`. The command fails
on page errors, horizontal document overflow, and JavaScript errors, and writes
screenshots and measurements under `artifacts/ui-review`.

## Failure classification

Every failure must be classified as one of:

1. implementation defect;
2. stale test;
3. schema/fixture defect;
4. intentional business-rule change;
5. environment/configuration defect.

There is no permanent "known failure" category.

## Business-rule changes

If a business rule intentionally changes, update the test to express the approved new rule and document the decision. Do not make a test pass by weakening the assertion without an approved rule change.
