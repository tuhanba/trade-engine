---
name: build
description: >
  Read the spec in specs/<name>.md and implement exactly what it describes —
  no extra features, no unrelated refactors, no invented requirements — then
  report which spec requirements were covered so a review step can check them.
  Use when the user runs /build, or asks to "build", "implement", or "code up"
  a spec that already exists under specs/.
---

# /build — Build exactly to spec

Your job: implement **exactly** what a spec under `specs/` describes — nothing
more, nothing less — then report which requirements you covered so a review step
can check them against the build.

## Hard rules (do not break these)

1. **Read the spec fully first.** Do not write any code until you've read the
   entire spec and understand it.
2. **Build only what's in the spec.** Do not add features that aren't
   specified. Do not refactor or "clean up" unrelated code. Do not invent
   requirements, behaviors, or improvements the spec doesn't call for.
3. **Honor constraints and non-goals.** Use the tech stack / platform the spec
   names. Do not implement anything the spec lists as out of scope.
4. **Don't guess on gaps.** If a requirement is ambiguous, contradictory, or
   missing a detail you need, ask the user one focused question rather than
   inventing behavior. Surface anything under the spec's **Open questions** —
   don't silently resolve it. For minor, low-risk gaps, proceed but record the
   assumption in the coverage report.
5. **Minimal footprint on shared code.** If satisfying the spec requires
   touching existing code, change only what's necessary; leave unrelated code
   alone.
6. **Don't commit or push** unless the user asks.

## Steps

1. **Locate the spec.**
   - If the user passed a name, read `specs/<name>.md`.
   - If not, look in `specs/`. If exactly one spec exists, use it. If several,
     ask which one. If none, tell the user to run `/spec` first and stop.
2. **Parse the spec.** Identify the **Objective**, each **Requirement**
   (R1, R2, … with Must/Should), **Constraints / non-goals**, **Edge cases**
   (E1, E2, …), and the **Definition of done**. If the spec doesn't use IDs,
   reference each item by its text or heading instead.
3. **Plan briefly, then implement** requirement by requirement. Match the
   surrounding code's conventions and style. Handle every edge case the spec
   lists.
4. **Verify against the Definition of done** wherever you can — e.g. run the
   tests or build the spec calls for, and confirm the edge-case behavior. Report
   what you actually ran and its result.
5. **Report coverage** (format below). Then stop — don't pick up unrelated work.

## Coverage report (the handoff to review)

End your turn with a checklist that maps **every** spec item to its status and
where it lives, so a reviewer can check each one against the diff:

```markdown
## Build coverage for specs/<name>.md

### Requirements
- [x] R1 (Must): <text> — <file:line or brief note on where/how>
- [x] R2 (Must): <text> — <where implemented>
- [ ] R3 (Should): <text> — NOT done (<why / deferred>)

### Edge cases
- [x] E1: <situation> — handled in <where>
- [ ] E2: <situation> — NOT handled (<why>)

### Definition of done
- [x] <criterion> — <how it's satisfied / what you ran to verify>
- [ ] <criterion> — <not met / blocked, and why>

### Out of scope (intentionally not done)
- <non-goal from the spec, restated so the reviewer knows it was deliberate>

### Assumptions / open questions
- <any minor gap you proceeded on, or ambiguity you flagged>
```

Mark items honestly: use `[x]` only for work that is actually done and, where
possible, verified. If you couldn't finish something, mark it `[ ]` and say why
— never claim coverage you didn't deliver. The review step relies on this list
being accurate.
