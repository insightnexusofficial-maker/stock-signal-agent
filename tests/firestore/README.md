# Push preference rules checks

The tests use `demo-sayo-preferences`, localhost port 8786 and synthetic registration records. They never query production registrations or send notifications.

Install the pinned SDK dependencies in this directory, then run with a Java runtime and Firebase CLI available:

```sh
npm install --ignore-scripts --no-audit --no-fund
firebase emulators:exec --project demo-sayo-preferences --config firebase.json --only firestore "npm test"
```

The existing registration token is the capability identifier used by the current anonymous registration flow. The added write path permits only the push boolean and its update timestamp; approval, token, nickname, registration fields, lists and deletion remain outside it. Enabling anonymous preference updates does not establish authenticated user ownership of that token. Do not log or expose identifiers.
