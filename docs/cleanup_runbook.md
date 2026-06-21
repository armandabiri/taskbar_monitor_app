# Resource Cleanup Runbook

This guide explains how the **Resource Cleanup** actions behave, why a run can
look like it "did nothing", and how to make it reclaim memory on demand.

All actions live in the app menu under **Resource Cleanup** (right-click the
taskbar monitor). The two toolbar buttons are 🧠 **Smart** and ⚡ **Aggressive**.

## Why a normal run can clean nothing

Each profile has a **pressure threshold** (percent of RAM used). When system
memory is *below* that threshold, a non-aggressive Smart run deliberately does
only a light pass — on a healthy system that often means **0 processes cleaned**.
This is by design (it avoids churning the disk when there is no pressure), but it
reads as "the button does nothing".

The result dialog now states this in plain language, e.g.:

> Nothing needed cleaning: system memory is below the profile's pressure
> threshold, so only a light pass ran. Use Force a full pass to reclaim anyway.

## Actions

| Action | What it does |
| --- | --- |
| 🧠 Smart / ⚡ Aggressive | Run the bound profile. Respects the pressure threshold (Smart) or runs hard (Aggressive). |
| ⚡ **Force Reclaim Now** | Runs a full pass **bypassing the pressure threshold** — always trims eligible processes. Safety guards (foreground, keep-list, protected names/users, new-process grace) still apply. |
| 🔍 **Preview cleanup…** | Dry run: scans and scores, **executes nothing**, and lists ranked candidates with an estimated reclaim. Confirm with *Run now* or *Run forced*. |
| 🧹 **Flush standby cache** | Purges the Windows standby file cache to free RAM immediately. Needs administrator privileges; you get a clear message if it can't run. |
| ↩ **Reset throttled processes** | Restores the priority/affinity of any processes a previous cleanup throttled. Enabled only when there are throttled processes to restore. |
| 🤖 **Auto-clean settings…** | Configure the auto-clean watchdog (below). |

## Live progress and cancel

Any run shows a small always-on-top overlay with the current phase
(scan → trim → throttle → kill → flush) and a **Cancel** button. Cancelling
stops the run at the next batch boundary; nothing further is executed.

## Measured vs estimated reclaim

The result reports two numbers:

- **Estimated freed** — summed from each working-set trim (Windows may re-page
  some of this).
- **Measured system delta** — the actual change in available RAM sampled after a
  short settle. Labelled a *system delta* because other processes also allocate
  concurrently, so it is not exclusively "freed by cleanup".

## Auto-clean watchdog

Opt-in and **off by default**. When enabled, it watches the RAM samples the app
already collects and fires a **forced Smart cleanup** when memory used stays at
or above a threshold for a debounce window, then waits out a cooldown before it
can fire again. Configure enable / threshold / sustained-for / cooldown in
**Resource Cleanup → Auto-clean settings…**.

## Safety bounds (deadline, kill budget, flush timeout)

`CleanupBounds` guards every run against getting stuck:

| QSettings key | Default | Meaning |
| --- | --- | --- |
| `cleanup/deadline_s` | 120 | Hard wall-clock deadline for the whole run. |
| `cleanup/kill_budget_s` | 30 | Total time the kill phase may spend across all targets. |
| `cleanup/per_kill_graceful_s` | 3 | Graceful-terminate wait per process. |
| `cleanup/per_kill_force_s` | 5 | Force-kill wait per process. |
| `cleanup/max_candidates` | 50 | Maximum processes the kill phase will consider. |
| `cleanup/flush_timeout_s` | 10 | Per-flush-call timeout (system flush opt-in only). |
| `cleanup/enable_system_flush` | false | Opt-in to flush modified pages / working sets / standby cache during a run (same as the manual Flush standby cache action, applied automatically at the end). |

Runs that hit the deadline or kill-budget cap are reported as partial: the result
dialog lists how many candidates were skipped and why.

## Cancellation guarantees

Pressing **Cancel** during a run (or an automatic cancel from the watchdog):

- The cancel signal is checked *between* every process in the trim, throttle, and
  kill loops — so at most one more process is touched after you press Cancel.
- The post-run settle (system-RAM measurement) also aborts immediately on cancel.
- The result dialog marks the run as cancelled and reports whatever was completed.

## Run watchdog

If a run is still active `deadline_s + 5 s` after it started, the watchdog
automatically calls Cancel and fires a desktop notification. This prevents a hung
background thread from blocking the next cleanup or app exit.

## Troubleshooting

- **"I pressed cleanup and nothing happened."** The system was below the pressure
  threshold. Use **Force Reclaim Now**, or click **Force a full pass** in the
  result dialog. Use **Preview cleanup…** first if you want to see what it will do.
- **"Cleanup takes forever."** The scan walks every process; USS lookups for large
  processes now run in parallel. Use the progress overlay to watch it, and
  **Cancel** if needed. Force/Preview both run on a background thread, so the UI
  stays responsive.
- **"Flush standby cache did nothing."** It requires administrator privileges.
  Relaunch the app as administrator, or run a profile that includes the standby
  flush.
- **"A throttled app feels sluggish after cleanup."** Use **Reset throttled
  processes** to restore its priority and CPU affinity.
- **Protected apps are never touched.** Foreground app, keep-list entries,
  protected system names/users, and very new processes are always spared — even
  on a forced run. Force only bypasses the *pressure* gate, not the protections.
