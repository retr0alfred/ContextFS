# Hardening report `p5-final2`

Packaged build `ContextFS.exe` sha256[:16] = `079996801794ae0d`

| Case | Source | Result | Seconds |
|---|---|---|---|
| C01 | ADR-0011 §1-§4, ADR-0016: read-only, formats, names, long paths, malformed and oversized files | PASS | 8.8 |
| C02 | ADR-0011 §2 + Rev. 1: junction loops are never descended | PASS | 1.8 |
| C03 | ADR-0011 §3: offline/placeholder files are never opened | PASS | 1.8 |
| C04 | ADR-0011 §4: a locked file becomes an error row and is retried next scan | PASS | 3.0 |
| C05 | ADR-0011 §2: an unreadable folder is an error, and never tombstones its files | PASS | 2.0 |
| C06 | ADR-0011 §4 test: Word-style save loop (temp write + ReplaceFileW) x1000 during indexing | PASS | 18.6 |
| C07 | ADR-0011 'no lost scan after a kill', ADR-0012, ADR-0014 test 10: kill x5, then incremental == clean | FAIL | 174.5 |
| C08 | ADR-0012 §1: a second writer is refused; the first is unaffected | PASS | 36.2 |
| C11 | ADR-0001 §2: a native parser crash (access violation) is quarantined after two strikes | PASS | 6.0 |
| C12 | ADR-0012 §3: a damaged database is detected, set aside and rebuilt; search recovers | PASS | 70.1 |
| C13 | ADR-0012 §3: truncated vector files are repaired; search never crashes | PASS | 21.0 |
| C14 | ADR-0012 §4: low disk refuses to start and leaves the index untouched | PASS | 13.4 |
| C17 | Decision 95: the frozen self test passes | PASS | 1.5 |
| C18 | Phase 33 real-data finding: a folder removed from Settings leaves search (packaged child) | PASS | 4.8 |
| C19 | Phase 33 real-data finding: closing mid-scan never leaves files without dates | PASS | 27.7 |
| C20 | QA H1 (Phase 33): a renamed or deleted folder leaves no ghost results | PASS | 4.3 |
| C21 | QA H2+H3 (Phase 33): 1601- and pre-1970-dated files never stop a scan or crash the window | PASS | 11.1 |
| C22 | QA L1 (Phase 33): support commands answer in JSON, even for a missing index | PASS | 4.0 |
