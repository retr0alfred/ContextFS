# Transfer table T — measured 2026-10-27T16:06:14+00:00

Machine: Windows-11-10.0.26200-SP0, NTFS (D:). Source FS times: created < modified < accessed, all in 2025. `kept` = unchanged, `now` = set to transfer time, `source_current` = copied the source's value at copy time (source atime moves when tools read it).

| tool | birth | mtime | atime | birth>mtime | in-file times | MotW |
|---|---|---|---|---|---|---|
| copy_item_ps | now | kept | now | True | all kept | False |
| cmd_copy | now | kept | now | True | all kept | False |
| xcopy | now | kept | now | True | all kept | False |
| robocopy_default_DAT | kept | kept | other(2026-10-27T16:04:02.519822+00:00)/source_current | False | all kept | False |
| robocopy_COPY_D | kept | kept | other(2026-10-27T16:04:02.519822+00:00)/source_current | False | all kept | False |
| shell_copyhere_explorer_engine | now | kept | now | True | all kept | False |
| shell_movehere_same_volume | kept | kept | kept | False | all kept | False |
| shutil_copy | now | now | now | False | all kept | False |
| shutil_copy2 | now | kept | now | True | all kept | False |
| shutil_copyfile | now | now | now | False | all kept | False |
| compress_expand_archive_ps | now | kept | now | True | all kept | False |
| python_zipfile_extractall | now | now | now | False | all kept | False |
| tar_zip_roundtrip | now | kept | source_current | True | all kept | False |
| git_clone | now | now | now | False | all kept | False |
| safe_save_ntfs_tunnel | kept | now | now | False | all kept | False |
| in_place_edit | kept | now | now | False | all kept | False |
| python_docx_resave | now | now | now | False | all kept | False |

Formats per tool: txt, docx, pdf, jpg, zip.

## Pending (needs the user)

- `explorer_drag_copy_gui`: Explorer drag-and-drop copy (GUI) — the Shell COM row is the scripted proxy
- `onedrive_roundtrip`: Upload to OneDrive, delete local, re-download (Files-On-Demand hydrate)
- `usb_fat32_roundtrip`: Copy to a FAT32 USB stick and back
- `usb_exfat_roundtrip`: Copy to an exFAT USB stick and back
- `browser_download`: Download the file from a web page/mail attachment in a browser (Mark-of-the-Web)
- `word_save_as`: Open in Microsoft Word and Save As a new name (rewrites core.xml)
- `sevenzip_roundtrip`: 7-Zip archive and extract
- `whatsapp_telegram_desktop`: Receive the file through a messenger desktop client

## Not covered

- cross-volume moves (would need a second writable volume; C: is excluded by project rule)
- network shares / SMB
