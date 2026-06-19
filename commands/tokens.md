---
description: Review the token ledger and report spend trends
---

Read `<MEMORY_DIR>/token_ledger.md` and report:

1. Total estimated spend and total sessions logged.
2. Average cost per session, and the 3 most expensive sessions.
3. Cache hit-rate trend (low hit rates = cache being busted — flag it).
4. Model mix — how much spend is going to each tier. Per the token-economy rule, a high top-tier share is a signal to delegate more.
5. One concrete tuning suggestion based on what the data shows (e.g. "the top tier is 80% of spend — route more file-reading to a cheaper model").

If the ledger doesn't exist yet, say so — it populates as sessions end (SessionEnd hook) or when `token-ledger.py` is run manually on a transcript.

Keep it short and decision-oriented. This is the measurement feedback loop for the token-economy rule.
