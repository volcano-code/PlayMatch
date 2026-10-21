# Development instructions

Read docs/implementation-contract.md before changing an interface. Keep Python and TypeScript contracts synchronized. Do not fabricate live prices, verified metadata, working notifications, or test results. Preserve region and edition uncertainty. Keep secrets out of source, fixtures, logs and URLs. Test relevant behavior before publishing. Use deterministic provider fixtures in CI, and label the optional live probe separately. Store project deliverables in this Git repository.

Each development increment must publish the complete source changes, tests, migrations,
configuration, operating documentation and truthful validation record to volcano-code/PlayMatch.
Use a feature branch and CI before merging; do not leave new work only in chat or local archives.
Never upload credentials, private runtime records, databases, dependencies or model weights.
