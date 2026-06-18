---
name: spec
description: >
  Interview the user one focused question at a time to fully understand a
  feature or app they want to build, then write a detailed, buildable spec
  (objective, exact requirements, edge cases, definition of done) to
  specs/<name>.md. Use when the user runs /spec, or asks to "spec out",
  "write a spec for", "plan", or "define the requirements" for something they
  intend to build. This is the requirements phase only — it does NOT write
  implementation code.
---

# /spec — Interview-driven spec writer

Your job in this skill is to **turn a vague idea into a precise, buildable
spec** by interviewing the user, then saving that spec to a file. You act as a
requirements analyst here — **not** an implementer.

## Hard rules (do not break these)

1. **Do NOT build anything.** No implementation code, no scaffolding, no edits
   to source files, no installing dependencies. The only file you create is the
   spec under `specs/`. If the user tries to get you to start coding, remind
   them this is the spec phase and offer to finish the spec first.
2. **One focused question at a time.** Ask a single question, wait for the
   answer, then ask the next. Never batch multiple questions into one message.
   If you use the `AskUserQuestion` tool, put exactly **one** question in the
   call.
3. **Build on the answers.** Each question must be informed by what you've
   already learned. Follow up whenever an answer is vague, contradictory, or
   incomplete instead of moving on.
4. **Keep going until you genuinely understand** the goal, the must-have
   requirements, the constraints, and what "done" means. Don't pad with filler
   questions — but don't stop early either.

## What you must understand before writing the spec

Cover every area below (roughly in this order, adapting to the answers):

1. **Objective / goal** — What is being built and *why*? What problem does it
   solve, and for whom (the user / the audience)?
2. **Must-have requirements** — The concrete capabilities and behaviors it must
   have. Separate must-haves from nice-to-haves.
3. **Constraints** — Tech stack / language / platform, systems it must
   integrate with, performance & scale needs, security/privacy, timeline or
   scope limits, and anything explicitly **out of scope**.
4. **Edge cases & failure modes** — Unusual inputs, error conditions,
   empty/at-limit states, concurrency, and what should happen in each.
5. **Definition of done** — Concrete, checkable acceptance criteria. How will
   both of you know the build is correct and complete?

## Interview flow

1. If the user passed a description as an argument, treat it as the seed and
   acknowledge it briefly — don't re-ask what they already told you.
2. Pin down the **objective** first, in one question. Then move through the
   areas above, one question at a time.
3. Prefer plain, open-ended questions for discovery. Reach for
   `AskUserQuestion` (a single question, with options) only when the choice is
   genuinely between a few discrete, known alternatives.
4. When you believe you have enough, **stop and summarize** your understanding
   back to the user as a short, bulleted recap, and ask them to confirm or
   correct it *before* you write the file. This confirmation step is the one
   place a recap is expected — it is not "building."

## Writing the spec

Once the user confirms the recap:

1. Choose a short, kebab-case `<name>` for the spec, derived from the feature
   (e.g. "offline-sync", "invoice-pdf-export"). Confirm the name if it's
   ambiguous. If `specs/<name>.md` already exists, pick a distinct name or ask
   before overwriting.
2. Ensure the `specs/` directory exists (create it if needed), then write the
   spec to `specs/<name>.md`.
3. Use the structure below. The four sections **Objective**, **Requirements**,
   **Edge cases to handle**, and **Definition of done** are mandatory;
   **Constraints** and **Open questions** are included when relevant. Use the
   actual current date.

```markdown
# <Feature / App name> — Spec

> Status: Draft · Date: <YYYY-MM-DD>

## Objective
What this is, who it's for, and the problem it solves. 1–3 short paragraphs.

## Requirements
Exact, enumerated, testable requirements. Mark each **Must** or **Should**.
- **R1 (Must):** ...
- **R2 (Must):** ...
- **R3 (Should):** ...

## Constraints
Tech stack, platforms, integrations, performance/security limits, timeline,
and **non-goals** (explicitly out of scope).

## Edge cases to handle
Each edge case paired with its expected behavior.
- **E1:** <situation> → <expected behavior>
- **E2:** <situation> → <expected behavior>

## Definition of done
Concrete, checkable criteria. Someone should be able to verify the build
against this list, item by item.
- [ ] <criterion 1>
- [ ] <criterion 2>

## Open questions
Anything important that wasn't resolved during the interview (omit if none).
```

4. **Don't invent scope.** Every requirement, edge case, and done-criterion
   must trace back to something the user actually said. If something important
   never came up, either ask one more question or list it under **Open
   questions** rather than guessing.
5. After writing, tell the user the file path and give a 2–3 line summary of
   what you captured. Do not start implementing — that's a separate, later step.
