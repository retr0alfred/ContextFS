# Hardening report `p5-20261028`

Packaged build `ContextFS.exe` sha256[:16] = `17533d2bb21c4def`

| Case | Source | Result | Seconds |
|---|---|---|---|
| C01 | ADR-0011 §1-§4, ADR-0016: read-only, formats, names, long paths, malformed and oversized files | PASS | 10.9 |
| C02 | ADR-0011 §2 + Rev. 1: junction loops are never descended | PASS | 6.8 |
| C03 | ADR-0011 §3: offline/placeholder files are never opened | PASS | 2.9 |
| C04 | ADR-0011 §4: a locked file becomes an error row and is retried next scan | PASS | 4.3 |
| C05 | ADR-0011 §2: an unreadable folder is an error, and never tombstones its files | PASS | 4.5 |
| C06 | ADR-0011 §4 test: Word-style save loop (temp write + ReplaceFileW) x1000 during indexing | PASS | 87.4 |
| C08 | ADR-0012 §1: a second writer is refused; the first is unaffected | PASS | 219.6 |
| C09 | ADR-0001 §2: the indexer child cannot outlive the window (Job Object) | PASS | 771.3 |
| C10 | ADR-0001 §2: a file that hangs a parser costs restarts, then is quarantined (GUI-driven) | FAIL | 78.1 |
| C11 | ADR-0001 §2: a native parser crash (access violation) is quarantined after two strikes | PASS | 36.7 |
| C12 | ADR-0012 §3: a damaged database is detected, set aside and rebuilt; search recovers | PASS | 292.6 |
| C13 | ADR-0012 §3: truncated vector files are repaired; search never crashes | PASS | 103.4 |
| C14 | ADR-0012 §4: low disk refuses to start and leaves the index untouched | PASS | 28.2 |
| C17 | Decision 95: the frozen self test passes | PASS | 1.7 |
| C18 | Phase 33 real-data finding: a folder removed from Settings leaves search (packaged child) | PASS | 17.3 |
| C19 | Phase 33 real-data finding: closing mid-scan never leaves files without dates | PASS | 291.2 |
