CATALOG_SCHEMA_VERSION = "3"
RUNTIME_SCHEMA_VERSION = "1"


CATALOG_SCHEMA = r"""
PRAGMA foreign_keys = ON;

CREATE TABLE players (
    base_player_ea_id INTEGER PRIMARY KEY,
    slug TEXT,
    common_name TEXT NOT NULL,
    first_name TEXT,
    last_name TEXT,
    nickname TEXT,
    gender INTEGER,
    nation_id INTEGER,
    foot TEXT,
    height_cm INTEGER,
    age INTEGER,
    FOREIGN KEY (nation_id) REFERENCES nations(nation_id)
);

CREATE TABLE nations (
    nation_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL
);

CREATE TABLE leagues (
    league_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL
);

CREATE TABLE clubs (
    club_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL
);

CREATE TABLE positions (
    position_id INTEGER PRIMARY KEY,
    code TEXT NOT NULL UNIQUE
);

CREATE TABLE rarities (
    rarity_id INTEGER NOT NULL,
    quality TEXT NOT NULL,
    rarity_ea_id INTEGER,
    name TEXT NOT NULL,
    PRIMARY KEY (rarity_id, quality)
);

CREATE TABLE cards (
    card_ea_id INTEGER PRIMARY KEY,
    futgg_id INTEGER NOT NULL UNIQUE,
    base_player_ea_id INTEGER NOT NULL,
    slug TEXT,
    card_name TEXT,
    overall INTEGER NOT NULL,
    quality TEXT NOT NULL,
    rarity_id INTEGER NOT NULL,
    club_id INTEGER,
    league_id INTEGER NOT NULL,
    weak_foot INTEGER,
    skill_moves INTEGER,
    pace INTEGER,
    shooting INTEGER,
    passing INTEGER,
    dribbling INTEGER,
    defending INTEGER,
    physicality INTEGER,
    gk_diving INTEGER,
    gk_handling INTEGER,
    gk_kicking INTEGER,
    gk_reflexes INTEGER,
    gk_speed INTEGER,
    gk_positioning INTEGER,
    strength INTEGER,
    accelerate_type TEXT,
    total_igs INTEGER,
    is_icon INTEGER NOT NULL DEFAULT 0 CHECK (is_icon IN (0, 1)),
    is_hero INTEGER NOT NULL DEFAULT 0 CHECK (is_hero IN (0, 1)),
    is_special INTEGER NOT NULL DEFAULT 0 CHECK (is_special IN (0, 1)),
    is_dynamic INTEGER NOT NULL DEFAULT 0 CHECK (is_dynamic IN (0, 1)),
    is_evolution INTEGER NOT NULL DEFAULT 0 CHECK (is_evolution IN (0, 1)),
    is_sbc INTEGER NOT NULL DEFAULT 0 CHECK (is_sbc IN (0, 1)),
    is_objective INTEGER NOT NULL DEFAULT 0 CHECK (is_objective IN (0, 1)),
    created_at TEXT,
    image_url TEXT,
    card_image_url TEXT,
    FOREIGN KEY (base_player_ea_id) REFERENCES players(base_player_ea_id),
    FOREIGN KEY (rarity_id, quality) REFERENCES rarities(rarity_id, quality),
    FOREIGN KEY (club_id) REFERENCES clubs(club_id),
    FOREIGN KEY (league_id) REFERENCES leagues(league_id)
);

CREATE TABLE card_positions (
    card_ea_id INTEGER NOT NULL,
    position_id INTEGER NOT NULL,
    is_primary INTEGER NOT NULL DEFAULT 0 CHECK (is_primary IN (0, 1)),
    PRIMARY KEY (card_ea_id, position_id),
    FOREIGN KEY (card_ea_id) REFERENCES cards(card_ea_id) ON DELETE CASCADE,
    FOREIGN KEY (position_id) REFERENCES positions(position_id)
);

CREATE UNIQUE INDEX one_primary_position_per_card
ON card_positions(card_ea_id) WHERE is_primary = 1;

CREATE TABLE playstyles (
    playstyle_ea_id INTEGER PRIMARY KEY,
    futgg_id INTEGER UNIQUE,
    name TEXT NOT NULL,
    category TEXT,
    who_has_it TEXT,
    description TEXT,
    plus_description TEXT,
    image_url TEXT
);

CREATE TABLE card_playstyles (
    card_ea_id INTEGER NOT NULL,
    playstyle_ea_id INTEGER NOT NULL,
    tier INTEGER NOT NULL CHECK (tier IN (1, 2)),
    PRIMARY KEY (card_ea_id, playstyle_ea_id, tier),
    FOREIGN KEY (card_ea_id) REFERENCES cards(card_ea_id) ON DELETE CASCADE,
    FOREIGN KEY (playstyle_ea_id) REFERENCES playstyles(playstyle_ea_id)
);

CREATE TABLE roles (
    role_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    slug TEXT,
    position_id INTEGER,
    description TEXT,
    plus_ea_id INTEGER UNIQUE,
    plus_plus_ea_id INTEGER UNIQUE,
    focus_json TEXT,
    FOREIGN KEY (position_id) REFERENCES positions(position_id)
);

CREATE TABLE card_roles (
    card_ea_id INTEGER NOT NULL,
    role_id INTEGER NOT NULL,
    tier INTEGER NOT NULL CHECK (tier IN (1, 2)),
    PRIMARY KEY (card_ea_id, role_id, tier),
    FOREIGN KEY (card_ea_id) REFERENCES cards(card_ea_id) ON DELETE CASCADE,
    FOREIGN KEY (role_id) REFERENCES roles(role_id)
);

CREATE TABLE catalog_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE VIEW player_card_summary AS
SELECT base_player_ea_id,
       COUNT(*) AS card_count,
       MIN(overall) AS min_overall,
       MAX(overall) AS max_overall
FROM cards
GROUP BY base_player_ea_id;

CREATE INDEX cards_player_idx ON cards(base_player_ea_id);
CREATE INDEX cards_overall_idx ON cards(overall);
CREATE INDEX cards_club_idx ON cards(club_id);
CREATE INDEX cards_league_idx ON cards(league_id);
CREATE INDEX players_name_idx ON players(common_name);
CREATE INDEX card_positions_position_idx ON card_positions(position_id, card_ea_id);
CREATE INDEX card_playstyles_lookup_idx ON card_playstyles(playstyle_ea_id, tier, card_ea_id);
CREATE INDEX card_roles_lookup_idx ON card_roles(role_id, tier, card_ea_id);
"""


RUNTIME_SCHEMA = r"""
PRAGMA foreign_keys = ON;

CREATE TABLE runtime_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE account_state (
    persona_id TEXT PRIMARY KEY,
    club_id INTEGER,
    club_name TEXT,
    platform TEXT NOT NULL,
    coin_balance INTEGER,
    coin_observed_at TEXT,
    last_full_sync_id INTEGER,
    last_full_sync_at TEXT
);

CREATE TABLE sync_runs (
    sync_id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    error_code TEXT,
    error_message TEXT
);

CREATE TABLE sync_parts (
    sync_id INTEGER NOT NULL,
    area TEXT NOT NULL,
    page_count INTEGER NOT NULL DEFAULT 0,
    item_count INTEGER NOT NULL DEFAULT 0,
    complete INTEGER NOT NULL DEFAULT 0 CHECK (complete IN (0, 1)),
    error_code TEXT,
    PRIMARY KEY (sync_id, area),
    FOREIGN KEY (sync_id) REFERENCES sync_runs(sync_id) ON DELETE CASCADE
);

CREATE TABLE club_items (
    item_id INTEGER PRIMARY KEY,
    card_ea_id INTEGER NOT NULL,
    location TEXT NOT NULL,
    tradeable INTEGER NOT NULL CHECK (tradeable IN (0, 1)),
    loan_uses_remaining INTEGER,
    acquisition_cost INTEGER,
    protected INTEGER NOT NULL DEFAULT 0 CHECK (protected IN (0, 1)),
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    last_sync_id INTEGER NOT NULL,
    FOREIGN KEY (last_sync_id) REFERENCES sync_runs(sync_id)
);

CREATE TABLE inventory_changes (
    sync_id INTEGER NOT NULL,
    item_id INTEGER NOT NULL,
    change_type TEXT NOT NULL,
    from_location TEXT,
    to_location TEXT,
    details_json TEXT,
    PRIMARY KEY (sync_id, item_id, change_type),
    FOREIGN KEY (sync_id) REFERENCES sync_runs(sync_id) ON DELETE CASCADE
);

CREATE TABLE trade_listings (
    trade_id INTEGER PRIMARY KEY,
    item_id INTEGER NOT NULL,
    starting_bid INTEGER,
    buy_now_price INTEGER,
    current_bid INTEGER,
    status TEXT NOT NULL,
    listed_at TEXT,
    expires_at TEXT,
    closed_at TEXT,
    sold_price INTEGER,
    last_seen_at TEXT NOT NULL
);

CREATE TABLE reference_prices (
    card_ea_id INTEGER NOT NULL,
    platform TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    price INTEGER,
    status TEXT,
    PRIMARY KEY (card_ea_id, platform, observed_at)
);

CREATE TABLE market_scans (
    scan_id INTEGER PRIMARY KEY AUTOINCREMENT,
    card_ea_id INTEGER NOT NULL,
    observed_at TEXT NOT NULL,
    sample_count INTEGER NOT NULL,
    min_buy_now INTEGER,
    median_buy_now INTEGER,
    p25_buy_now INTEGER,
    p75_buy_now INTEGER,
    min_bid INTEGER
);

CREATE TABLE action_batches (
    batch_id TEXT PRIMARY KEY,
    execution_mode TEXT NOT NULL,
    expected_sync_id INTEGER,
    created_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL
);

CREATE TABLE actions (
    action_id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL,
    sequence_no INTEGER NOT NULL,
    action_type TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    item_id INTEGER,
    trade_id INTEGER,
    card_ea_id INTEGER,
    params_json TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    error_code TEXT,
    error_message TEXT,
    result_json TEXT,
    FOREIGN KEY (batch_id) REFERENCES action_batches(batch_id)
);

CREATE TABLE coin_transactions (
    transaction_id TEXT PRIMARY KEY,
    action_id TEXT,
    item_id INTEGER,
    card_ea_id INTEGER,
    kind TEXT NOT NULL,
    price INTEGER,
    tax INTEGER NOT NULL DEFAULT 0,
    coin_delta INTEGER NOT NULL,
    observed_at TEXT NOT NULL
);

CREATE TABLE sbc_sets (
    set_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT,
    expires_at TEXT,
    observed_at TEXT NOT NULL,
    raw_json TEXT NOT NULL
);

CREATE TABLE sbc_challenges (
    challenge_id TEXT PRIMARY KEY,
    set_id TEXT NOT NULL,
    name TEXT NOT NULL,
    status TEXT,
    repeatable INTEGER NOT NULL DEFAULT 0 CHECK (repeatable IN (0, 1)),
    requirements_json TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    FOREIGN KEY (set_id) REFERENCES sbc_sets(set_id)
);

CREATE TABLE sbc_solutions (
    solution_id TEXT PRIMARY KEY,
    challenge_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    estimated_cost INTEGER,
    tradeable_value INTEGER,
    objective_json TEXT NOT NULL,
    validation_json TEXT NOT NULL,
    status TEXT NOT NULL,
    FOREIGN KEY (challenge_id) REFERENCES sbc_challenges(challenge_id)
);

CREATE TABLE sbc_solution_items (
    solution_id TEXT NOT NULL,
    slot_index INTEGER NOT NULL,
    item_id INTEGER NOT NULL,
    PRIMARY KEY (solution_id, slot_index),
    UNIQUE (solution_id, item_id),
    FOREIGN KEY (solution_id) REFERENCES sbc_solutions(solution_id) ON DELETE CASCADE
);

CREATE INDEX club_items_card_idx ON club_items(card_ea_id);
CREATE INDEX club_items_location_idx ON club_items(location);
CREATE INDEX reference_prices_lookup_idx ON reference_prices(card_ea_id, platform, observed_at DESC);
CREATE INDEX market_scans_lookup_idx ON market_scans(card_ea_id, observed_at DESC);
CREATE INDEX actions_batch_idx ON actions(batch_id, sequence_no);
"""
