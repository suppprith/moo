"""Deep-research engine.

Wraps the moo pipeline in a planner -> iterative multi-hop retrieval loop ->
cited report assembler, so an agent can hand off a whole research question and
get back a grounded, confidence-scored report. Built stage by stage:

- ``plan`` decompose a question into sub-questions + an evidence plan
- ``loop`` iterate retrieval, chasing gaps and contradictions
- ``session`` persist a resumable run
- ``report`` assemble the cited report
"""
