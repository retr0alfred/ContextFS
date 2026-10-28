# Hardening report `p6-regression`

Packaged build `ContextFS.exe` sha256[:16] = `69861a1c0c1f2505`

| Case | Source | Result | Seconds |
|---|---|---|---|
| C01 | ADR-0011 §1-§4, ADR-0016: read-only, formats, names, long paths, malformed and oversized files | PASS | 8.5 |
| C02 | ADR-0011 §2 + Rev. 1: junction loops are never descended | PASS | 1.6 |
| C03 | ADR-0011 §3: offline/placeholder files are never opened | PASS | 1.6 |
| C04 | ADR-0011 §4: a locked file becomes an error row and is retried next scan | PASS | 2.6 |
| C05 | ADR-0011 §2: an unreadable folder is an error, and never tombstones its files | PASS | 1.8 |
| C06 | ADR-0011 §4 test: Word-style save loop (temp write + ReplaceFileW) x1000 during indexing | PASS | 23.1 |
| C07 | ADR-0011 'no lost scan after a kill', ADR-0012, ADR-0014 test 10: kill x5, then incremental == clean | PASS | 224.2 |
| C08 | ADR-0012 §1: a second writer is refused; the first is unaffected | PASS | 47.6 |
| C09 | ADR-0001 §2: the indexer child cannot outlive the window (Job Object) | PASS | 164.1 |
| C10 | ADR-0001 §2: a file that hangs a parser costs restarts, then is quarantined (GUI-driven) | PASS | 29.6 |
| C11 | ADR-0001 §2: a native parser crash (access violation) is quarantined after two strikes | PASS | 6.7 |
| C12 | ADR-0012 §3: a damaged database is detected, set aside and rebuilt; search recovers | PASS | 59.4 |
| C13 | ADR-0012 §3: truncated vector files are repaired; search never crashes | PASS | 20.8 |
| C14 | ADR-0012 §4: low disk refuses to start and leaves the index untouched | PASS | 5.4 |
| C15 | ADR-0013/0003 scale: 10^4-scale index time, memory, search latency, no-op launch update | PASS | 473.7 |
| C16 | ADR-0001: no event-loop stall > 250 ms while indexing and searching (packaged GUI) | FAIL | 175.9 |
| C17 | Decision 95: the frozen self test passes | PASS | 1.4 |
| C18 | Phase 33 real-data finding: a folder removed from Settings leaves search (packaged child) | PASS | 4.8 |
| C19 | Phase 33 real-data finding: closing mid-scan never leaves files without dates | PASS | 22.2 |
| C20 | QA H1 (Phase 33): a renamed or deleted folder leaves no ghost results | PASS | 3.9 |
| C21 | QA H2+H3 (Phase 33): 1601- and pre-1970-dated files never stop a scan or crash the window | PASS | 9.0 |
| C22 | QA L1 (Phase 33): support commands answer in JSON, even for a missing index | PASS | 0.6 |
