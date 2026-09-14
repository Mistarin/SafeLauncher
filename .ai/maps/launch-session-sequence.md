# Launch and Session Sequence

```text
library selection
 → prelaunch checks/diagnostics
 → prefix/runtime/environment preparation
 → sandbox runner
 → host process/session
 → playtime/session checkpoints
 → process exit
 → finalized local playtime
 → optional private metadata sync
```

Remote resource loading is advisory to this sequence. Launch security and process ownership remain in the runtime modules.
