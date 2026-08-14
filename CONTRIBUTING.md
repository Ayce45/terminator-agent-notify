# Contributing

## Development setup

Use Python 3 and pytest. The automated suite wraps Terminator and D-Bus
boundaries with fakes, so it does not require a graphical display.

```bash
python -m pip install pytest
pytest -v
find . -type f -name '*.sh' -print0 | xargs -0 bash -n
python -m compileall -q core adapters terminator-plugin
git diff --check
```

Run the release manual checks in [tests/manual-checklist.md](tests/manual-checklist.md)
on a real desktop before a release. Do not record a GUI check as passed from a
headless test run.

## Change guidelines

- Preserve hooks and configuration not marked `terminator-agent-notify:`.
- Keep notification and hook failures fail-safe: an unavailable service must
  not silently approve a permission or break normal terminal interaction.
- Treat Codex auto-resume as experimental and fail closed when its transcript
  format or timing is uncertain.
- Keep runtime state private and namespace it by agent, session, and request.
- Do not change the imported Claude artwork or represent it as MIT-licensed;
  `assets/claude.png` and `assets/claude.svg` are Anthropic property and are
  outside this repository's MIT license.

Add or update fixtures for real, minimally-redacted hook payload variations.
Tests must bound child-process waits so a broken hook cannot stall CI. Run the
full non-graphical verification commands above before requesting review.

## Documentation changes

When behavior changes, update the README's command examples, defaults,
security/fallback semantics, log locations, uninstall behavior, and migration
notes. Keep `tests/test_baseline_layout.py` as the public README contract and
extend it for new user-facing commands or safety guarantees.
