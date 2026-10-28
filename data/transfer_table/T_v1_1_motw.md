# T v1.1 addendum — Mark-of-the-Web survival (measured 2026-10-28T21:17:08+00:00)

Machine: Windows-11-10.0.26200-SP0, NTFS (C:). Each fixture carried `Zone.Identifier` (ZoneId=3) before the tool ran.

| tool | MotW survives | formats |
|---|---|---|
| copy_item_ps | yes | txt, docx, pdf, jpg, zip |
| cmd_copy | yes | txt, docx, pdf, jpg, zip |
| xcopy | yes | txt, docx, pdf, jpg, zip |
| robocopy_default_DAT | yes | txt, docx, pdf, jpg, zip |
| robocopy_COPY_D | yes | txt, docx, pdf, jpg, zip |
| shell_copyhere_explorer_engine | yes | txt, docx, pdf, jpg, zip |
| shell_movehere_same_volume | yes | txt, docx, pdf, jpg, zip |
| shutil_copy | no | txt, docx, pdf, jpg, zip |
| shutil_copy2 | yes | txt, docx, pdf, jpg, zip |
| shutil_copyfile | no | txt, docx, pdf, jpg, zip |
| compress_expand_archive_ps | no | txt, docx, pdf, jpg, zip |
| python_zipfile_extractall | no | txt, docx, pdf, jpg, zip |
| tar_zip_roundtrip | no | txt, docx, pdf, jpg, zip |
| git_clone | no | txt, docx, pdf, jpg, zip |
| safe_save_ntfs_tunnel | no | txt, docx, pdf, jpg, zip |
| in_place_edit | yes | txt, docx, pdf, jpg, zip |
| python_docx_resave | no | docx |
