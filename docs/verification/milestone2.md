# Milestone 2 verification

> Superseded model reference: this accurately records the CPX400DP material used
> at the time. It is not authority for QPX600DP implementation; see
> `model_correction_qpx600dp.md`.

Date: 2026-09-08. Scope: manual-derived command reference and architecture design.
Prerequisite: milestone 1 committed as `1f9031f` after successful verification.

## Evidence review

| Evidence | Verification |
| --- | --- |
| Manual identification | CPX400DP instruction manual, 48511-1480, Issue 1; 34 PDF pages |
| Source integrity | Copied bytes match attachment; SHA-256 recorded in command reference |
| Specifications and power envelope | Read printed pp.2-4, 12-14; visually inspected p.2 |
| Interface ownership and remote-state effects | Read pp.17-19 |
| USB/RS232 framing, timing and responses | Read pp.19, 27-31 |
| Command syntax | Read and visually checked printed pp.28-29; reviewed pp.30-31 |
| Destructive status reads and errors | Read pp.23-26 and 30-31 |
| Future LAN extension | Read pp.20-21; TCP 9221, limited VXI-11 |
| Selected syntax/page anchor check | 22 anchors found on the stated printed pages |
| Documentation links | Relative targets checked locally |
| Code regression | All 8 milestone 1 tests passed again |
| Application launch | Qt offscreen event loop, visible window, exit code 0 |

## Review conclusions

The reference distinguishes programming increments from meter resolution and
latched event bits from instantaneous status. It records IFLOCK/IFUNLOCK replies,
explicit independent-mode selection, OCP/OVP differences, parser blocking,
unregulated power-envelope behaviour and the absence of a documented watchdog.

The architecture proposes one physical connection owner, no I/O in GUI callbacks,
per-channel scheduling and cancellation generations, short atomic query/reply
transactions, bounded display buffers and independent durable data writers.
These mechanisms remain design work; none was implemented in this delivery.

Remaining commissioning questions include exact real-unit readback formatting,
USB flow-control behaviour, safe acquisition cadence, firmware/driver compatibility,
the full accepted OCP range and physical OFF response. No hardware or Windows
test has been performed. This evidence establishes a verified starting point,
not a completed production control system.
