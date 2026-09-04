# Frontend testing guide

Use npm and choose the narrowest relevant check first.

- `npm run test:local-contract` verifies committed local configuration.
- `npm run build` runs the supported Webpack production build.
- `npm run smoke:local` checks frontend and backend readiness after both are
  running.
- `python3 -B -m unittest discover -s tools/multica/tests -p 'test_*.py' -v`
  runs the Eventra Multica control-plane suite.

For behavior changes, first add a focused failing test and record the expected
RED result. After the smallest implementation, rerun the focused test and the
relevant broader suite. Evidence includes exact commands and exit codes.
