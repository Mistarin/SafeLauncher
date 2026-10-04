# Launch and Session Sequence

```text
library selection
 → PrelaunchController lock and cloud-save resolution
 → conflict/quota choice or local-save fallback
 → prefix/runtime/environment preparation
 → LaunchSessionCoordinator sandbox runner and durable session record
 → GameSessionController tracker activation
 → process/session observation and managed stop task
 → tracker completion and terminal session finalization
 → SessionFeatureController cleanup before terminal session release
 → AchievementSyncController bounded delayed local exit read
 → optional private metadata sync
```

Remote resource loading is advisory to this sequence. Launch security and process ownership remain in the runtime modules.
