# CAN capture fixtures

Real captures from the solar car bus, kept as golden test data for the decoders.
Format: Vector ASC (as written by the decode scripts' `can.ASCWriter`).

| File | Source | Contents |
| --- | --- | --- |
| `bestgo-capture.asc` | 2026-05-30, BESTGO live on shared 500K bus | standard 11-bit IDs 0x351-0x374 (limits, SOC, measurements, alarms, name/info, cell extremes) |
| `ezkontrol-capture.asc` | 2026-05-16, EZkontrol on PC at 250K | extended IDs 0x180117EF (Message I) and 0x180217EF (Message II), ~250 frames each |

Day-to-day capture/probe logs are NOT committed (see root `.gitignore`); only
these curated files are. If you add a fixture, note its provenance here.
