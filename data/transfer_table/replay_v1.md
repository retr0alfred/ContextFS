# Simulator-fidelity replay v1 (2026-10-29T08:09:23+00:00)

Agreement between the simulated damage and the real tool, per field (kept vs reset; MotW present vs absent).

| behaviour (tool) | n | birth | mtime | in-file | MotW | errors |
|---|---|---|---|---|---|---|
| content_copy (shutil.copyfile) | 58 | 1.00 (real reset, sim reset) | 1.00 (real reset, sim reset) | 1.00 (real kept, sim kept) | 1.00 (real False, sim False) | 0 |
| copyfile (Copy-Item) | 80 | 1.00 (real reset, sim reset) | 1.00 (real kept, sim kept) | 1.00 (real kept, sim kept) | 1.00 (real False/True, sim False/True) | 0 |
| git_clone (git clone) | 10 | 1.00 (real reset, sim reset) | 1.00 (real reset, sim reset) | 1.00 (real kept, sim kept) | 1.00 (real False, sim False) | 0 |
| robocopy (robocopy) | 14 | 1.00 (real kept, sim kept) | 1.00 (real kept, sim kept) | 1.00 (real kept, sim kept) | 1.00 (real False/True, sim False/True) | 0 |
