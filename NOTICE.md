# Notice

FC27 Copilot is an unofficial, self-hosted integration for EA SPORTS FC Ultimate Team Web App state and FUT.GG catalog data.

It is not affiliated with or endorsed by Electronic Arts, FUT.GG, FUTBIN, or the authors of referenced community extensions.

EA Web App endpoints are undocumented and may change. Automated access may violate current service terms or trigger account restrictions. The project does not bypass captcha, verification, transfer restrictions, rate limits, or other access controls.

Third-party source material remains subject to its own license and terms. Imported implementation must retain applicable notices and be reviewed before public distribution.

## License scope

The project's original code and documentation are licensed under the MIT License in `LICENSE`. That license does not relicense third-party source, grant rights to EA or FUT.GG databases, card artwork, player images, trademarks, or authorize access to their services. Google OR-Tools retains its own license and notices. The original input archives are listed in `docs/source-artifacts.md`; their provenance record is not permission to redistribute them.

## Deployment and confirmation

This is a self-hosted, single-user local integration, not a public hosted service. Account-writing features are available under policy limits, but every new write batch requires the Agent to explain the exact operation, ask the user, and receive explicit approval. `confirmed=true` declares that approval; the server does not independently prove a human approved it. Text instructions and tool annotations are not a guarantee of Agent behavior. Use a client with visible write approval and keep service ports local.

## Data and diagnostics

The repository includes `data/catalog.sqlite`, a FUT.GG-derived player-card snapshot, and its validation manifest. This third-party dataset is not covered by the project's MIT license, and inclusion does not grant additional data or service-access rights. The snapshot is dated; it is not a live catalog or a price feed.

Account databases, session credentials, logs, and personal development records must not be bundled in releases. Regression fixtures use synthetic or redacted account identities and preserve necessary public challenge facts. Review diagnostic material before sharing it. Checks on the current checkout do not inspect every prior Git commit, issue, attachment, or external copy. Repository-history cleanup does not erase hosting caches, pull-request references, issue attachments, or copies already held by others.

No separate database redistribution grant is supplied by this repository. Its presence and source metadata are not evidence of additional permission. Users and redistributors must establish the applicable provider permissions independently; the MIT license covers original project code and documentation only.

EA's rules prohibit bots, automation, and auto-buyers; FUT.GG's linked service terms restrict automated access and copying except where expressly permitted. MIT licensing does not remove those restrictions or account risk. Verify the applicable rules and permissions before use. The project makes no guarantee of account safety, profit, complete SBC support, or global minimum market cost.

Official terms: https://help.ea.com/en/articles/ea-sports-fc/fc-rules/ and https://stormstrike.gg/terms.
