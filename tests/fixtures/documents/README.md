# Real document regression fixtures

This directory contains small, deterministic files used to verify the real PDF
and DOCX ingestion path. The text is intentionally unique so retrieval tests can
prove that both formats remain usable inside one Project without relying on a
network model.

## Fixture inventory

| File | Expected content | SHA-256 |
| --- | --- | --- |
| `atlas-release-notes.pdf` | The Atlas code word is `SILVER BLOOM`, and the review date is 14 October 2026. | `e9cb58f6e27427d49d62202e160cd5f1a99b87d4bda56c9c978689a79f3f76f0` |
| `lumen-operations-handbook.docx` | The Lumen calibration value is `42.75`, with a monthly review cadence. | `5931007fd91ba91df65a8cff25d663f07e2d2c09099a8313475ad3c0ce007d40` |

The PDF is a one-page, unencrypted Letter document with extractable text. The
DOCX is a standard OOXML package containing prose and a two-column table. ZIP
member timestamps, document properties, and PDF metadata are fixed so a rebuild
produces the same bytes.

## Provenance and attribution

Elysia AI contributors authored all visible text specifically for these tests.
The files do not reproduce or adapt third-party prose, branding, images, fonts,
or datasets. ReportLab and python-docx generated the containers; those tools do
not contribute copyrighted fixture content.

Optional attribution: `Elysia AI real-document regression fixtures`.

## License

To the extent possible under law, the fixture authors waive all copyright and
related rights in these two files under
[CC0 1.0 Universal](https://creativecommons.org/publicdomain/zero/1.0/).
They may be copied, published, modified, and redistributed independently of the
repository's source-code license status.
