# Hardening report `p5-final2`

Packaged build `ContextFS.exe` sha256[:16] = `079996801794ae0d`

| Case | Source | Result | Seconds |
|---|---|---|---|
| C01 | ADR-0011 §1-§4, ADR-0016: read-only, formats, names, long paths, malformed and oversized files | PASS | 8.8 |
| C02 | ADR-0011 §2 + Rev. 1: junction loops are never descended | PASS | 1.8 |
| C03 | ADR-0011 §3: offline/placeholder files are never opened | PASS | 1.8 |
| C04 | ADR-0011 §4: a locked file becomes an error row and is retried next scan | PASS | 3.0 |
| C05 | ADR-0011 §2: an unreadable folder is an error, and never tombstones its files | PASS | 2.0 |
| C14 | ADR-0012 §4: low disk refuses to start and leaves the index untouched | PASS | 13.4 |
| C17 | Decision 95: the frozen self test passes | PASS | 1.5 |
| C18 | Phase 33 real-data finding: a folder removed from Settings leaves search (packaged child) | PASS | 4.8 |
| C22 | QA L1 (Phase 33): support commands answer in JSON, even for a missing index | PASS | 4.0 |
