---
name: review
description: >
  Compare the current build against specs/<name>.md requirement by requirement,
  listing every gap, bug, and missing piece while naming the exact spec item
  each one fails, then write specific fixes and hand them back so /build can
  address them. Passes the build only when every requirement in the spec is
  fully met. Use when the user runs /review, or asks to "review", "check", or
  "verify" a build against its spec.
---

# /review — Review the build against its spec

Your job: check the **current build** against the spec in `specs/`, requirement
by requirement, find every gap, bug, and missing piece — naming the exact spec
item each one fails — and hand back specific fixes for `/build`. **Pass only
when every requirement in the spec is fully met.**

## Hard rules (do not break these)

1. **Read the spec fully first, then inspect the actual build.** Base every
   finding on the real code and behavior — not on any self-reported coverage.
   You may use `/build`'s coverage checklist as a map, but verify each claim
   independently; that independent check is the whole point of review.
2. **Do NOT fix anything.** Review only inspects and reports. "Writing the
   fixes" means describing each change precisely enough that `/build` can apply
   it — it does **not** mean editing source code yourself. Leave the working
   tree unchanged.
3. **Check every spec item explicitly.** Each Requirement (R#, Must/Should),
   each Edge case (E#), each Constraint / non-goal, and each Definition-of-done
   criterion gets an explicit **PASS** or **FAIL**, each with evidence
   (`file:line`, or the command you ran and its result).
4. **Name the exact spec item for every finding.** Every gap, bug, or missing
   piece must cite the spec item it fails (e.g. "fails **R3**", "**E2** not
   handled", "violates non-goal"). No vague findings.
5. **Pass = 100%.** The verdict is **PASS** only when every **Must**
   requirement, every edge case, and every definition-of-done item is fully met
   and verified. Report any unmet **Should** items too; unless the spec frames
   them as optional, an unmet item makes the verdict **CHANGES NEEDED**. If the
   spec explicitly marks something out of scope / future, that is not an unmet
   requirement.

## Steps

1. **Locate the spec.**
   - If the user passed a name, read `specs/<name>.md`.
   - Otherwise look in `specs/`: use the sole spec if there's one, ask which if
     there are several, or tell the user to run `/spec` first and stop.
2. **Identify what to review.** Inspect the implemented code: use
   `git diff` against the base branch to see what changed, read the relevant
   files, and **run the tests / build the spec's Definition of done calls for**.
   Note exactly what you ran and the result.
3. **Walk the spec top to bottom.** For each item, record a verdict with
   evidence; on FAIL, record the concrete gap and the exact fix that resolves
   it.
4. **Produce the review report** (format below).
5. **Give the verdict.** If CHANGES NEEDED, end with the consolidated fix list
   for `/build`. Do not apply fixes. The loop is: `/review` → `/build` (with
   these fixes) → `/review` again, until PASS.

## Review report (the handoff to /build)

```markdown
## Review of specs/<name>.md — <PASS | CHANGES NEEDED>

Reviewed: <branch / commit> · Ran: <test/build commands + result>

### Requirements
- [x] R1 (Must): <text> — PASS, verified in <file:line / how>
- [ ] R2 (Must): <text> — FAIL: <gap / bug / missing piece> → Fix: <specific change>
- [ ] R3 (Should): <text> — FAIL: <gap> → Fix: <specific change>

### Edge cases
- [x] E1: <situation> — PASS, handled in <file:line>
- [ ] E2: <situation> — FAIL: <what happens instead> → Fix: <specific change>

### Definition of done
- [x] <criterion> — PASS (<evidence / what you ran>)
- [ ] <criterion> — FAIL: <why> → Fix: <specific change>

### Constraints / non-goals
- [x] <constraint honored> — <evidence>
- [ ] <constraint / non-goal violated> → Fix: <remove / adjust>

### Out-of-spec work found (scope creep)
- <anything built that the spec didn't ask for> — <where>; recommend removal unless justified

### Fixes for /build
1. (R2) <actionable fix — which file/where, exactly what to change>
2. (E2) <actionable fix>
3. (DoD) <actionable fix>

### Verdict
<PASS — every requirement met, build is complete>
OR
<CHANGES NEEDED — N item(s) failing; run /build with the fixes above, then /review again>
```

Be precise and honest: mark `[x]` only for items you actually verified, and
`[ ]` for anything failing, partial, or unverifiable. The **Fixes for /build**
list is the contract for the next build pass — each fix must name its spec item
and state exactly what to change. Keep looping until every requirement passes.
