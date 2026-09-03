# 1Cpace V8 Testing Baseline

## Purpose

The automated test suite is a merge gate. A required test failure blocks the change.

## Controlled baseline

Run tests from a clean checkout with the project's declared dependencies installed. CI supplies explicit test-only configuration so results do not depend on a developer's local `.env`.

The historical baseline of **14 failures out of 177 tests** was observed in a clean environment without `.env`, `ENCRYPTION_KEY`, and `TESSERACT_CMD`. It is an environment-specific observation, not a universal test count.

## Local command

```text
python -m pytest -q
```

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
