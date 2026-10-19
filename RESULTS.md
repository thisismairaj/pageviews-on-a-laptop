# Failure-injection results

| Scenario | Expected | Result | Evidence |
|---|---|---|---|
| 1. kill -9 during download | resumes, final files byte-identical to source | PASS | killed mid-transfer: 2 partial file(s), 0 complete; after rerun all 3 files byte-identical to source=True, reconcile exit=0 |
| 2. kill -9 during load | half-done state is reported, rerun converges, no leftovers | PASS | killed mid-load: reconcile exit=1 (3 problem(s) reported, not hidden); after rerun exit=0, leftover .tmp files=0 |
| 3. truncated raw file | that hour fails loudly, others load, no partial output | PASS | load: 2 ok, 1 failed / reconcile exit=1, hour 1 parquet exists=False, problems=1 |
| 4. flipped bit in a raw file | that hour fails loudly, no partial output | PASS | load: 2 ok, 1 failed / reconcile exit=1, problems=1 |
| 5. same content under two names | reconcile flags the duplicate | PASS | reconcile exit=1, problems=['PROBLEM: duplicate content: pageviews-20240101-000000.gz and pageviews-20240101-010000.gz '] |
| 6. a missing hour | reconcile names the missing hour | PASS | reconcile exit=1, first problem: PROBLEM: 2024-01-01: missing hours [1] |
| 7. 600 garbage lines inside a valid file | all quarantined with reasons, rows in = loaded + quarantined | PASS | 600 injected lines (500 non-empty bad + 100 blank): in=5,198,321 loaded=5,197,721 quarantined=600 reconcile exit=0 |
| 8. bit rot in a Parquet file after load | reconcile reports it | PASS | reconcile exit=1, problems=['PROBLEM: pageviews-20240101-000000.gz: good/hour=00.parquet changed since it was written ('] |
| 9. two loaders at the same time | the second one refuses to start; the first finishes; reconcile OK | PASS | exit codes of the two loaders=[0, 3] (3 = refused, another pvl is working); reconcile exit=0, problems=[] |
