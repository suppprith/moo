# moo agent demo

**Question:** Postgres vs MySQL for complex analytical joins

## 1. deep_research — hand off the whole question

`run_id=f58ad19175fc` · status **partial** · grounded **7/7** (4 well-supported) · answer citations valid: True

**Executive answer**

  Don't use serial For new applications, identity columns should be used instead. [S1]
  PostgreSQL vs MySQL: an apples to oranges comparison. [S2][S3][S4][S5][S6][S7] If
  you're connecting to a database other than MySQL, there is a driver-specific second
  option that you can refer to (for example, and for PostgreSQL). [S3][S4][S8][S9][S10]
  Storing a value as a numeric, possibly with the currency being used in an adjacent
  column, might be better. [S1] Correctly setting up the connection PDO Note that when
  using PDO to access a MySQL database real prepared statements are not used by default.
  [S10][S11] Original (outdated) benchmark from 2011 I ran three tests with PostgreSQL
  9.1 on a real life table of 65579 rows and single-column btree indexes on each of the
  three columns involved and took the best execution time of 5 runs. [S12][S13]

**Findings**
- _(conf 0.4515)_ Don't use serial For new applications, identity columns should be used instead.  -> cites [1]  `clm_26`
- _(conf 0.6892)_ PostgreSQL vs MySQL: an apples to oranges comparison  -> cites [2, 3, 4, 5, 6, 7]  `clm_15`
- _(conf 0.6694)_ If you're connecting to a database other than MySQL, there is a driver-specific second option that y  -> cites [3, 4, 8, 9, 10]  `clm_16`
- _(conf 0.6493)_ Storing a value as a numeric, possibly with the currency being used in an adjacent column, might be   -> cites [1]  `clm_25`
- _(conf 0.4954)_ Correctly setting up the connection PDO Note that when using PDO to access a MySQL database real pre  -> cites [10, 11]  `clm_17`

## 2. get_claim('clm_26') — verify a finding's evidence

confidence 0.4515 · disputed True · 6 evidence edges
- contradicts `chk_48` (trust 0.889): Stick to using a-z, 0-9 and underscore for names and you never have to worry abo
- supports `chk_64` (trust 0.889): Storing a value as a numeric, possibly with the currency being used in an adjace
- supports `chk_59` (trust 0.889): ### When should you?

When you're porting very, very old software that uses fixe

## 3. fetch_source('doc_14') — read the source

- Don't Do This — https://wiki.postgresql.org/wiki/Don%27t_Do_This
  trust_tier via report · 31 chunks

## 4. list_contradictions('Postgres vs MySQL for complex analytical joins')

- disputes surfaced: **1**
  - If you want to obtain it from query instead of psql, you can query the catalog schema.

---
_Reproduce: `uv run python -m app.eval.demo`_
