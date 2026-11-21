# Twelve simulated users

Written 2026-11-17, **before the task pool exists.** Each persona is played by a model
(`claude-cli:sonnet`) that sees only:
- this persona;
- the task card's goal and facts, which are the person's own knowledge;
- what the assistant says to it.

A persona never sees CI's code, the arms, the hidden checks, the reference solutions or the
labels.

**What a persona does in a run:**
1. **Request.** It writes the request in its own voice, choosing which of its facts to mention.
   It reports which facts it included, and so whether a question is needed is labelled
   deterministically (`PROTOCOL.md` §3).
2. **Answers.** It answers each question the assistant asks, from its facts only.
3. **Corrections.** When the result is not what it wanted, it writes a correction in its own
   words. The hidden check decides whether the result is right.
4. **Rating.** It rates the experience (1–5) and says whether it would let the assistant do this
   unattended.

The grid covers four dimensions:
- **technical ability:** novice, intermediate or expert;
- **ambiguity pattern:** how much of what matters the person says;
- **goals:** what they optimise for;
- **error tolerance:** how a mistake, or a needless question, affects them.

| id | name (fictional) | ability | ambiguity pattern | goal | error tolerance | reacts to questions |
|---|---|---|---|---|---|---|
| P01 | Ada | expert | precise: states every essential fact and constraint | speed | low: one wrong change is a failure | a needless question annoys her |
| P02 | Bo | novice | terse: one short line, leaves out the specifics | get it done for him | high | glad to be asked; answers fully |
| P03 | Chen | intermediate | refers to shared history ("like last time", "the usual way") that the assistant cannot see | speed | medium | answers, briefly |
| P04 | Dana | expert | over-explains, burying the one essential fact in a long message | correctness | low | answers precisely |
| P05 | Eli | novice | uses the wrong technical words (says "delete" for "hide", "database" for a CSV) | learn while doing | high | likes explanations |
| P06 | Femi | intermediate | states two goals that pull against each other without noticing | correctness | medium | answers, sometimes changes their mind |
| P07 | Gus | intermediate | typos and shorthand, mostly complete | speed | high | answers in fragments |
| P08 | Hana | expert | complete about the code, silent about who outside should be told or what should be sent | delegation | low | answers precisely |
| P09 | Ivo | novice | vague goal ("make it better", "clean it up") | reassurance | medium | answers, but vaguely at first |
| P10 | Jun | intermediate | cautious: asks for a change and also says what must not be touched, but not always precisely | safety | very low: an unwanted removal is unacceptable | prefers to be asked before anything is removed |
| P11 | Kim | expert | delegates broadly ("handle it", "you know what to do") | speed | medium | dislikes questions, answers in one line |
| P12 | Lou | intermediate | mixes a question about how to do something with a request to do it | understanding | medium | answers fully |

## How each persona chooses what to say

The persona model is told its row, and it follows that row's pattern when it decides which of its
facts to include:
- the precise expert (P01) includes every fact the change depends on;
- the terse novice (P02) includes almost none;
- the history-referencer (P03) replaces one essential fact with a reference to "last time";
- and so on for the rest of the table.

It must not invent facts it was not given, and it must not hint at a fact it left out.

## Assignment

- Each persona gets 2 tasks from the frozen pool, 24 tasks in all.
- The assignment is drawn from a fixed seed after the pool is frozen, balanced so that each
  persona gets 2 different categories.
- Every task is then run under every arm with the **same request text**, so each comparison
  between arms is paired on the same person and the same words.

## Limits

These are models playing people. The table shapes their behaviour; it does not make them people.
`docs/release/HUMAN_EVAL.md` is the test with people, and nothing here replaces it.
