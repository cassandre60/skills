# Reviewing the fleet's own history

How a session's history becomes evidence for a change to the skill. This is
the method behind "The cadence" in `05`: when the work queue has nothing
meaningful left and the machine has spare capacity, the Orchestrator runs
this review and files what it finds.

## What this is, and what it is not

- **It is** a review of how the rules *behaved*: where a rule was missing,
  where a rule existed and was not followed, and where two rules
  contradicted each other.
- **It is not** running this skill's rounds against this skill's own code. A
  mutation round over `ledger.py` finds a bug in a script; it says nothing
  about whether the rule that script implements is the right rule. That
  work has its own stream and its own budget.
- **It is not** a second Stream L. Stream L (`08`) is a *stream*: a
  deterministic finder, a mechanical oracle, and replay-gated promotion of
  automated changes. This is a **duty with a method** — a session reads its
  own history and files a proposal. It does not need Stream L to be
  unlocked, and it does not ship one.

## The evidence surface

A session's history is **not** its journal alone. The journal is the
narrative record; the rest of the state dir holds the rest of the session,
and the most consequential events are often the ones nobody narrated.

| Source | What only it knows |
|---|---|
| the **journal** (append-only, timestamped) | what the fleet decided, and why |
| the **compaction-proof state file** | the rules the fleet learned and is now enforcing; the open items |
| the **nudge transcripts** and the **watchdog log** | what the fleet did *without being asked* — a wake it took by itself |
| the **snapshot accumulator** (`snapshots.jsonl`) | the no-model record of main's sha, the open PRs and the worktrees, kept while the session could read nothing |
| the **ledger** in the target repo | which targets were really audited, as the tool's own parser sees them |
| the **PR and issue timeline** | what actually landed, what was proposed, what was refused |

(Verified 2026-10-22: the merge a fleet took on the strength of a nudge
appears **once** in the nudge transcript and **zero times** in its journal.
The journal is the fleet's memory, and it did not hold the event. A review
that reads only the journal misses it.)

## The review loop

1. **Read the sources above**, oldest first, and write down every event that
   looks like a misfire: a wrong answer from a tool, a silent death, a
   conflict, a decision nobody authorised, a lesson written down and then
   not applied.
2. **Ask which rule was missing**, and where it would have to live. The
   five laws are the referee's: a rule that belongs there is an issue, not a
   PR.
3. **Ask whether the rule already existed.** If it did, the finding is
   different and more interesting: *the rule was not followed*, or *two
   rules contradicted each other*. Say which, and name the instrument that
   let the failure through.
4. **Ask whether the failure was visible in the data at the time.** If it
   was, a rule at the point of action would have caught it. If it was not
   visible anywhere, no rule could have: the fix is a rule about *looking* —
   an instrument, a check, or this cadence.
5. **File one proposal per rule**, with the evidence attached.

## What a proposal carries

| Field | What it says |
|---|---|
| **evidence** | the file, the line, the measurement — the event, not a summary of it |
| **scope** | the one rule, and the file it goes in |
| **the rule it would have prevented** | stated as the rule, so a reviewer can check whether that rule would really have worked |
| **already covered?** | "no rule existed" / "the rule existed and was not followed" / "two rules contradicted" — with the instrument named |
| **the referee test** | whether it touches the five laws, the stream contract, the eval sets, the tier floors or the merge policy |

## Where it lands

- A **rule change** is a PR against the skill: the rule, and **the test that
  asserts the rule is stated**. A rule with no test is a rule that decays.
- Anything that touches the **referee** is an **issue**, never a PR the
  fleet can merge: the five laws, the stream contract, the eval sets, the
  tier floors, the merge policy.
- A finding about the **product repo** is a normal fleet finding — an issue
  or a PR there, with the evidence contract in `04`. The review does not
  change the product; it changes the rules that shape the work.

## The measure

The review is worth doing if it changes a rule. It is not worth doing if it
only re-states one. A rule that already says the right thing and was not
followed needs a *different* fix — an instrument, a check, or a line in the
dispatch brief — and the proposal has to say which.
