# Testing improvements: before and after

Status: selected risk reviews and final local checks complete; source changes are not installed in the user's environment. This records confirmed
changes and measured evidence; it is not a percentage of all possible defects.

## Confirmed product defects: 9 reproduced, 9 fixed in the working tree

| Risk | Before | After and evidence |
| --- | --- | --- |
| Scheduled delivery | Concurrent delivery by independent services could write the same result twice. | One history message and one event in the two-process regression; removing its lock breaks the test. |
| Worker heartbeat | Cleanup could finalize a live `processing` or `cancelling` task after a fresh heartbeat. | Both states survive the controlled interleaving; truly stale tasks still finalize. Each removed freshness check is detected. |
| Initial mobile submission | Failure after creating a task but before writing its history caused a retry to enqueue the same text as a follow-up. | Retry restores the original submission, event and receipt without adding a steer entry; tests include process death and already claimed tasks. |
| Follow-up mobile submission | Failure after queue insertion could enqueue the same follow-up twice on retry. | Queue and recovery record commit together; retry restores history and one event, including after consumption. Tests cover shared and separate mobile stores. |
| Concurrent messages | Different simultaneous messages for an idle session could create two tasks. | Two actual processes produce one task and one queued follow-up; a separate-session test checks independent progress. |
| Partial history write | A recovered message was joined to a damaged unterminated JSON line and became unreadable. | A separator preserves existing bytes, and line-by-line UTF-8 decoding skips damaged records; initial, follow-up, truncated UTF-8 and valid unterminated records pass. |
| Mobile response finalization | A retry after writing an answer but before marking the task finalized wrote the same answer twice. | A durable turn identifier prevents a duplicate on retry, including process death, while identical answers in distinct follow-up rounds are retained. An interrupted attachment event is restored. |
| Pending message resumption | A failure after marking a follow-up message consumed but before requeueing its task left the message outside the queue and unprocessed. | The task transition and queue consumption now commit together, with a check that the pending snapshot is unchanged. Failure during prompt preparation preserves the message; an injected SQLite failure rolls the task transition back. |
| Orphan message recovery | A failure after creating the replacement task but before updating history caused retry to create a second task for the same message. | The created task carries the original queue identities and is reused on retry, including after process death. Only the selected queue entries are consumed; an entry arriving after the snapshot stays pending. |

A separate issue in the test infrastructure was fixed: missing or malformed JUnit
reports no longer crash the runner or count as success. A schedule fixture also
no longer fetches external model catalogues. A separate-store regression
introduced during this work was found and corrected; it is not counted as a
pre-existing product defect.

## Measurements

- Python tree before the subsequent 0.2.27 version and release-note changes: all **577 files** executed in two groups, **11,413 passed, 21 skipped,
  906 subtests passed, 0 failed/errors**. The six guest-path-sensitive files used
  `/tmp`; the remaining files used a separate short directory. The groups used
  one and two workers respectively, not CI's four. Hashes of **1,550** source
  files were unchanged during the run. The environment was separately synced
  from `uv.lock`; the two-worker split differs from CI's four-worker command.
- The 21 skips comprise six Windows-specific cases and 15 KVM/vsock-related
  cases. They are not passes. A separate live KVM attempt did not boot the guest.
- Desktop: **521 passed** using locked dependencies in an isolated copy after
  adding the redirect assertion (the earlier baseline had 520). Android:
  **483 JVM tests passed**, plus lint and debug build in a separate copy of the
  then-current Android tree. These do not establish native UI, physical-device
  or Windows acceptance.
- A wheel built from a separate source inventory was installed into an isolated
  locked environment. All **405** packaged Python modules matched source bytes;
  **10** recovery tests passed against the installed package. Wheel version is
  **0.2.26**; this is not an installed Desktop upgrade or rollback test.
- Earlier mutation sample: **18 of 18 deliberately selected mutants detected** across
  file writing, HTTP authorization, delivery, heartbeat, stream termination and
  session/message locking. This is a hand-selected sample, not a project-wide
  mutation score. Invalid earlier runs that imported the wrong source are not
  counted; the event-deduplication mutant caused an error and rollback rather
  than an observed duplicate event.
- One additional Desktop redirect mutation survived the old selected test and
  was detected after a new assertion checked that the foreign request was never
  sent. The original passes both tests. This is a separate targeted sample.
- After the two latest mobile recovery fixes, the focused mobile, queue, worker,
  media and recovery suite passed **149 tests and 5 subtests**. This does not
  replace a full run of the current tree.
- Following the orphan-recovery fix, the related mobile, queue and worker suite
  passed **135 tests and 5 subtests**; the subsequently extended focused
  recovery file passed **10 tests**; the subsequent full run includes them.
- Coverage reports were generated, but two native data files could not be
  combined. Do not present their aggregate percentage or a delta as a reliable
  coverage improvement.

## Remaining limitations

Using existing evidence and adding checks for distinct material gaps, **11 of 11**
mapped areas have a review of selected risks and limitations recorded. The full run had
**15** KVM/vsock and **6** Windows skips; actual guest isolation and Windows
acceptance remain unverified. Physical Android, real external providers,
power-loss durability, current-build Desktop installation/update/rollback and
orphan requeue-event recovery are also not established. These counts are work
boundaries, not a percentage of all possible defects or scenarios.

Evidence: [detailed report](testing-quality-2026-10-27.md),
[repository risk map](repository-risk-map-2026-10-27.md),
[TestLOG](../TestLOG.md), and local run artifacts under
`build/verification-testing-quality-20261027/`.

Test uniqueness: four selected faults escaped earlier checks and were caught by new ones; one overlapping new test was removed. See [the comparison](test-uniqueness-audit-2026-10-27.md).
