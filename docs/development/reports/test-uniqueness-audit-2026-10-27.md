# Test uniqueness audit — 2026-10-27

The question is whether a new check exercises a failure or a system boundary
not already established by the selected earlier tests. Similar names or covered
lines alone are not enough. This is a scoped review, not proof that no other
case anywhere in the repository overlaps.

## Controlled old-versus-new comparisons

For each fault below, selected earlier and new tests passed on the unmodified
code. The same single fault was then injected in a separate source copy. The
selected earlier tests still passed; the new regression failed for the stated
behavior. Logs: `build/verification-testing-quality-20261027/dedup-*.log`.

| Failure model | Earlier tests | New evidence |
| --- | --- | --- |
| Remove the lock around scheduled delivery's history check and append | Two earlier sequential/retry cases passed with the fault. | The new interleaving detects the duplicate; the retained two-process test also detects the fault after its readiness synchronization was corrected. |
| Ignore a refreshed heartbeat in the cancellation cleanup update | Eleven earlier worker recovery cases passed with the fault; they refresh before cleanup begins. | The new test refreshes after selection and detects the incorrect finalization. The similar manual-cleanup test exercises a different implementation. |
| Disable recovery of a keyed initial mobile submission | The earlier successful idempotent-repeat case passed with the fault. | A new test injects failure after task creation and detects the duplicate steer. |
| Remove the separator before appending to an unterminated history line | Three earlier concurrent-write cases passed with the fault. | New cases detect loss after partial writes and preserve a valid last record without a newline. |

These comparisons show incremental detection for the selected earlier tests;
they do not establish that every old test was run against each fault.

## Overlap found and action

Two new scheduled-delivery cases checked the same duplicate-delivery race, one
using threads and one using processes. The thread case was removed. The retained
process test now signals readiness after the service has completed its setup,
checks the source path in each child, and verifies one history message and one
event. Its original readiness signal was too early: a slow setup could let the
no-lock mutant pass. After correcting that, the baseline passed and the no-lock
mutant failed at the intended interleaving. The separate process-death recovery
case remains because it checks a different failure boundary.

Other new scenarios have related older tests but differ in the specific boundary:
file-write interruption versus successful replacement; actual child death versus
simulated delivery exception; and a mobile-to-worker-to-synthetic-provider path
versus an executor or HTTP component tested independently. Further overlap
review remains warranted; test totals are not an objective.
