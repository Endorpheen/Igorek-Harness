# Risk-based testing methodology

## Purpose

Protect user data, trustworthy agent actions and dependable recovery. Testing
should find consequential faults and give maintainers evidence about remaining
risk; test counts and coverage percentages are not goals in themselves.

## Scope and priorities

- Inventory the whole repository, including runtime, clients, security boundaries,
  build and deployment. Choose depth by likelihood and impact; prioritize data
  preservation, authorization, isolation, task execution and recovery.
- For each priority, identify the user scenario, expected behavior and failure
  conditions before choosing tests. Use coverage to locate unexercised paths,
  not as proof that assertions detect faults. Do not add tests solely to raise a
  metric or weaken existing checks.

## Methods

- **Small and larger tests:** combine focused tests with integration and end-to-end
  paths through real components where useful. Use isolated data and controlled
  substitutes for external services; state which parts were real or simulated.
- **Mutation testing:** introduce plausible, risk-specific faults one at a time
  in an isolated copy. Verify the original passes, the intended code was actually
  mutated and the test detects the fault. Investigate survivors; distinguish
  detected, surviving, equivalent, invalid and timed-out mutations. A sample
  score describes only that sample.
- **Failure and recovery:** define expected behavior and stop conditions, then
  inject representative failures such as interruption, unavailable storage,
  disconnect or restart. Check data, durable state, retry behavior and duplicate
  effects. Use disposable resources; do not disrupt user services or data.
- **Concurrency and security:** exercise competing processes, timeouts and
  repeated runs, and test unauthorized actions, permissions, path escapes and
  isolation. A clean finite sample does not prove races or vulnerabilities are
  absent. Test actual platform boundaries separately from simulated ones.
- **AI assistance:** use AI to propose failure classes, mutants and tests. Review
  them against the implementation and independently execute the original and
  changed behavior; generated assertions are not evidence by themselves.

## Acceptance and reporting

Fix confirmed defects and retain meaningful regression tests. For each relevant
risk, record the tested source and environment, scenario, commands, outcomes,
mutation classifications, repetitions, skips and unresolved limitations. Keep
unit, integration, native UI, VM and physical-device evidence distinct; a skip
is not a pass. Report what was actually observed and what remains unverified.
Use [TestLOG](TestLOG.md) for results and [the current risk map](reports/repository-risk-map-2026-10-27.md)
for the investigation; neither replaces this methodology.

## Sources

- [Google: Code Coverage Best Practices](https://testing.googleblog.com/2020/08/code-coverage-best-practices.html)
- [Google: Mutation Testing](https://testing.googleblog.com/2021/04/mutation-testing.html)
- [Microsoft: Architecture strategies for testing](https://learn.microsoft.com/en-us/azure/well-architected/operational-excellence/testing)
- [Meta: LLM-powered bug catchers](https://engineering.fb.com/2025/02/05/security/revolutionizing-software-testing-llm-powered-bug-catchers-meta-ach/)
- [Software Engineering at Google: Larger Testing](https://abseil.io/resources/swe-book/html/ch14.html)
- [AWS: Test resiliency using chaos engineering](https://docs.aws.amazon.com/wellarchitected/latest/reliability-pillar/rel_testing_resiliency_failure_injection_resiliency.html)
