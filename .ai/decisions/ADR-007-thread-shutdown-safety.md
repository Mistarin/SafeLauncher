# ADR-007: Strongly Own Worker Wrappers Through Shutdown

## Decision

Thread supervisors retain worker wrappers until controlled shutdown completes and avoid unsafe late destruction paths.

## Reason

Qt teardown can otherwise deliver delayed signals to deleted objects or destroy QThread wrappers while their native threads are still active.
