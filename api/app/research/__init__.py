"""Deep-research engine (Phase 11).

Wraps the moo pipeline in a planner -> iterative multi-hop retrieval loop ->
cited report assembler, so an agent can hand off a whole research question and
get back a grounded, confidence-scored report. Built stage by stage:

- ``plan``     (SUP-110) decompose a question into sub-questions + an evidence plan
- ``loop``     (SUP-111) iterate retrieval, chasing gaps and contradictions
- ``session``  (SUP-112) persist a resumable run
- ``report``   (SUP-113) assemble the cited report
"""
