# Test Reference: Acceptance Scenarios

Important cross-device/private-library scenarios:

- installed device and not-installed device share private account stats;
- cloud-only record becomes playable after installation without duplication;
- public profile does not expose installation state;
- stale cache renders while refresh succeeds or fails;
- duplicate requests share one loader;
- account switch rejects late old-context results;
- offline startup remains locally usable;
- active workers shut down without Qt aborts or late callbacks.
