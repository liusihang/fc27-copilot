# Source provenance

The initial integration used the following source inputs, recorded on September 18, 2026. This is a provenance record, not an installation requirement or a redistribution license. These input archives are not distributed with the project.

| Artifact | Size | SHA-256 | Intended use |
| --- | ---: | --- | --- |
| `fc27-copilot-full.zip` | 62,927 bytes | `9232d14776a57c9fafb568aa88e2c31de88373ebdf468e67663aaec54ce4f120` | Chrome session/EA adapter source |
| `fc27_v2_bundle.zip` | 6,083,559 bytes | `91fbdbcbccae95103dbcebc184df58c5eff8f5bda7ded6fbdaecce9b20b98bc5` | Initial FC27 catalog snapshot and updater |
| `fc27-mcp-portable.zip` | 11,442 bytes | `80ba0ee47114d786ea0afa7405d4b038d4731fb778f05c286f0ad7414bccff9d` | FUT.GG query and price adapter source |

The original SQLite source snapshot is not committed to Git. The normalized `data/catalog.sqlite` is included, and its corresponding validation report records the source, snapshot time, counts, and checksum. Catalog import and refresh are documented in the [data guide](../data/README.md).

The solver uses Google OR-Tools. Community research references include FUT Squad Lab, EAFC SBC Solver, and FUT AutoSBC; references are not authority for EA requirements or permission to relicense external code. Any incorporated third-party implementation must retain its own applicable license and notices. See [NOTICE](../NOTICE.md).
