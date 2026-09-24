# Configuration

No connection, safety or experiment defaults are loaded yet. Milestone 5 does
not persist or discover a serial port; its commissioning helper requires an
explicit `--port` on every invocation. Configuration
persistence is scheduled for milestone 17. Diagnostic logs default to the
project’s `logs/` directory; `--log-dir` provides an explicit override.

The QPX600DP ratings of 80 V, 50 A and 600 W are hardware capabilities and must
not be copied into future experiment safety defaults. The current simulator can
accept an explicit `UserSafetyLimits` object, but no persistent user limit is
created or inferred by this directory.
