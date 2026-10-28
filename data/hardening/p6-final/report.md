# Hardening report `p6-final`

Packaged build `ContextFS.exe` sha256[:16] = `f79cb80a1f0c7492`

| Case | Source | Result | Seconds |
|---|---|---|---|
| C01 | ADR-0011 §1-§4, ADR-0016: read-only, formats, names, long paths, malformed and oversized files | PASS | 8.7 |
| C02 | ADR-0011 §2 + Rev. 1: junction loops are never descended | PASS | 1.6 |
| C03 | ADR-0011 §3: offline/placeholder files are never opened | PASS | 1.6 |
| C04 | ADR-0011 §4: a locked file becomes an error row and is retried next scan | PASS | 2.6 |
| C05 | ADR-0011 §2: an unreadable folder is an error, and never tombstones its files | PASS | 2.0 |
| C06 | ADR-0011 §4 test: Word-style save loop (temp write + ReplaceFileW) x1000 during indexing | PASS | 18.6 |
| C07 | ADR-0011 'no lost scan after a kill', ADR-0012, ADR-0014 test 10: kill x5, then incremental == clean | PASS | 161.2 |
| C08 | ADR-0012 §1: a second writer is refused; the first is unaffected | PASS | 26.9 |
| C09 | ADR-0001 §2: the indexer child cannot outlive the window (Job Object) | PASS | 124.2 |
| C10 | ADR-0001 §2: a file that hangs a parser costs restarts, then is quarantined (GUI-driven) | PASS | 27.8 |
| C11 | ADR-0001 §2: a native parser crash (access violation) is quarantined after two strikes | PASS | 5.7 |
| C12 | ADR-0012 §3: a damaged database is detected, set aside and rebuilt; search recovers | PASS | 57.4 |
| C13 | ADR-0012 §3: truncated vector files are repaired; search never crashes | PASS | 18.3 |
| C14 | ADR-0012 §4: low disk refuses to start and leaves the index untouched | PASS | 5.5 |
| C15 | ADR-0013/0003 scale: 10^4-scale index time, memory, search latency, no-op launch update | PASS | 335.1 |
| C16 | ADR-0001: no event-loop stall > 250 ms while indexing and searching (packaged GUI) | PASS | 144.5 |
| C17 | Decision 95: the frozen self test passes | PASS | 1.5 |
| C18 | Phase 33 real-data finding: a folder removed from Settings leaves search (packaged child) | PASS | 5.6 |
| C19 | Phase 33 real-data finding: closing mid-scan never leaves files without dates | PASS | 23.0 |
| C20 | QA H1 (Phase 33): a renamed or deleted folder leaves no ghost results | PASS | 4.6 |
| C21 | QA H2+H3 (Phase 33): 1601- and pre-1970-dated files never stop a scan or crash the window | PASS | 9.6 |
| C22 | QA L1 (Phase 33): support commands answer in JSON, even for a missing index | PASS | 0.7 |
