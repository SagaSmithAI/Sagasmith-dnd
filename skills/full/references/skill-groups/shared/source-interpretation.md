# Bounded source interpretation

Use `continuity_context(purpose="source_interpretation")` only with current,
exact managed source evidence. Search summaries and model memory are not
evidence. The fresh evaluator copies and answers the exact signed question as
`source-interpretation-proposal.v1`, cites only claim-eligible refs, identifies
at least one evidence-bound claim, identifies ambiguity, and requires review
whenever an ambiguity or uncertain claim remains.

Call `bounded_evaluation(action="validate")`. A validated interpretation is
still a semantic proposal: it does not activate a rule, mutate state, or make a
mechanic executable. Standard-rule gaps require engine work; unresolved source
conflicts remain an external review boundary.
