# Grading rubric: live routing evaluation

This rubric belongs to `PREREGISTRATION.md` in this directory and is hashed with it
(`PREREG_HASHES.json`). `harness.py` sends the grader the text between the two markers below,
word for word, together with the request, the test author's criteria and reference answer,
the synthetic background notes, and one answer. Nothing outside the markers reaches the grader.

The grader is never told which arm, model, cost or latency produced an answer. Names an
assistant gives itself are replaced with "[assistant name withheld]" in every arm alike, before
grading. Each answer is graded on its own, in an order shuffled across arms and requests.

Tasks that carry a `check` (an exact value, a regular expression or required elements) are also
scored by that check, locally and without any model. The grader is not shown the check. The two
scores are reported side by side, and their disagreements are the measure of the grader's
reliability on that subset (PREREGISTRATION.md, "Grader independence").

<!-- grader-rubric:start -->
An answer succeeds only if all five rules hold.

1. Responsive. It does what the request asks, and every part of a request that has several parts. A clarifying question counts as responsive only when the request cannot sensibly be answered without one.
2. Correct. It contains no material error: no wrong fact, wrong calculation or wrong final answer, no code that would not run or would not do what was asked, and no advice that contradicts established practice. A slip that would not change what the person does is not material.
3. Meets the author's criteria. Every criterion listed for the request is met in substance, whatever the wording. Where a reference answer is given, an answer may differ from it in form, order or detail and still succeed, but it may not contradict it on a point the criteria or the request depend on.
4. Respects the request's explicit constraints: format, length, language, audience and scope. Treat length limits as guidance and fail an answer on length only when it clearly ignores one.
5. Does not refuse or deflect a reasonable request. If part of it cannot be done, the answer says so and does the rest.

These earn nothing and cost nothing:
- Length or thoroughness beyond what the request needs. Do not prefer the longer of two correct answers. Fail an answer for padding only when the padding hides the answer or breaks rule 4.
- Style, tone or formatting the request did not ask for.
- Using or mentioning a background note. The notes are true for this person.
- Whether the answer sounds like any particular assistant. You are not told who wrote it; do not guess.

Grade the request as it was asked, not against the best answer you can imagine. An answer that is short, plain and right succeeds.

serious_error is true when the answer contains an error that would likely harm a person who acted on it, or cost them significant time or money. Examples: code that silently corrupts data or opens a security hole, a wrong figure in a decision, or unsafe medical, legal, financial or security advice stated as fact. An answer with a serious error fails rule 2.
<!-- grader-rubric:end -->

## Notes for whoever reads the grades

- `failed_rules` lists the rule numbers above that the grader said the answer fails. It is
  reported, but no claim rests on it.
- An answer graded `success: true` with `serious_error: true` is scored as a failure. The rubric
  says a serious error fails rule 2. The harness counts these inconsistent grades and reports them.
