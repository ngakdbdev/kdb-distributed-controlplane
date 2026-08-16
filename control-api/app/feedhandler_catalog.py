"""
feedhandler_catalog.py - the control plane's provider registry for the
C++ feed-handler engine (data-plane/feedhandler-cpp/) - the admin-portal
counterpart to that engine's own FeedRegistry (core/FeedRegistry.hpp) and
config/providers/*.json default configs. Same "provider_catalog.py mirrors
data-plane/feeds/providers/, not the other way round" split this codebase
already uses for the Python feed simulators: the canonical protocol/venue
adapter code lives in the C++ tree, this is display + default-config data
for the UI, kept manually in sync (see that module's own docstring on the
same tradeoff).

Each entry's `default_config` is the exact FeedConfig-shaped JSON
data-plane/feedhandler-cpp ships under config/providers/ - activating a
provider starts from this, not a blank form.

`engine_support` is the honest flag on every entry:
  - "decoder_implemented": the engine has a real, tested decoder + venue
    adapter whose WIRE FORMAT genuinely matches this venue's protocol
    family (MoldUDP64+ITCH, SBE, FIX, WebSocket+JSON). Going live still
    needs that venue's own real entitlements/spec/schema/field mapping -
    see `requires` - but the protocol mechanics are not vaporware.
  - "catalog_only": listed for future integration (this task's own ask -
    "add all exchanges... for future integration purpose") because no
    decoder exists yet for that venue's actual wire protocol. Activating
    one of these in the admin portal is allowed (so it's visible/plannable)
    but there is nothing behind it in the engine yet - `protocol.type` is
    "not_yet_implemented" and the engine will refuse to run it.

FAST (FIX Adapted for STreaming) is deliberately NOT implemented as a
decoder here - it needs an XML-template-driven compression codec that's a
significant project on its own, not something to half-implement and ship
untested. Where a real venue's market-data leg is commonly FIX/FAST
(e.g. some Eurex/ICE products), the entry is filed under `catalog_only`
protocol_family "FIX/FAST" rather than silently pointed at the plain FIX
decoder (which cannot decode FAST-compressed messages).

Bloomberg B-PIPE and CRIMS are deliberately NOT in this catalog - they're
already handled by a separate, pre-existing simulated integration
(data-plane/feeds/bpipe_sim.py, crims_sim.py), unrelated to this C++ engine.
"""


def _implemented(provider, feed, display_name, tier, protocol_family, coverage, requires,
                  credentials_required, transport, protocol, venue_adapter,
                  recovery_enabled=True, ab_feed_enabled=True, field_mapping=None,
                  environment="production"):
    return {
        "provider": provider, "feed": feed, "display_name": display_name,
        "tier": tier, "protocol_family": protocol_family,
        "coverage": coverage, "requires": requires,
        "credentials_required": credentials_required,
        "engine_support": "decoder_implemented",
        "default_config": {
            "provider": provider, "feed": feed, "environment": environment, "enabled": False,
            "transport": transport, "protocol": protocol, "venue_adapter": venue_adapter,
            "recovery": {"enabled": recovery_enabled}, "ab_feed_enabled": ab_feed_enabled,
            "field_mapping": field_mapping or {},
        },
    }


def _future(provider, feed, display_name, protocol_family, coverage, requires,
            credentials_required=None):
    """Catalog entry for a venue with no working decoder yet - see module
    docstring. `requires` should say what real engine work is still needed,
    not just entitlements, so this never gets mistaken for a live path."""
    return {
        "provider": provider, "feed": feed, "display_name": display_name,
        "tier": "licensed", "protocol_family": protocol_family,
        "coverage": coverage, "requires": requires,
        "credentials_required": credentials_required or [],
        "engine_support": "catalog_only",
        "default_config": {
            "provider": provider, "feed": feed, "environment": "production", "enabled": False,
            "transport": {"type": "tbd"}, "protocol": {"type": "not_yet_implemented"},
            "venue_adapter": None, "recovery": {"enabled": False}, "field_mapping": {},
        },
    }


FEEDHANDLER_CATALOG = [
    {
        "provider": "COINBASE", "feed": "MATCHES", "display_name": "Coinbase (matches)",
        "tier": "public", "protocol_family": "WebSocket + JSON",
        "coverage": "crypto spot trades - real-time, no credentials required",
        "requires": "nothing - public feed",
        "credentials_required": [],
        "engine_support": "decoder_implemented",
        "default_config": {
            "provider": "COINBASE", "feed": "MATCHES", "environment": "production", "enabled": False,
            "transport": {
                "type": "websocket", "host": "ws-feed.exchange.coinbase.com", "port": "443", "path": "/",
                "subscribe_message": (
                    '{"type":"subscribe","product_ids":["BTC-USD","ETH-USD"],"channels":["matches"]}'
                ),
            },
            "protocol": {"type": "wsjson"},
            "venue_adapter": "generic_wsjson",
            "recovery": {"enabled": False},
            "field_mapping": {
                "filter_field": "type", "filter_value": "match",
                "field_symbol": "product_id", "field_price": "price", "field_qty": "size",
                "field_side": "side", "side_buy_value": "buy",
            },
        },
    },
    {
        "provider": "NASDAQ", "feed": "TOTALVIEW_ITCH", "display_name": "NASDAQ TotalView-ITCH",
        "tier": "licensed", "protocol_family": "MoldUDP64 + ITCH",
        "coverage": "US equities - full order-level depth (orders, executions, trades, symbol directory)",
        "requires": "a NASDAQ market-data agreement, entitlements, and multicast connectivity (cross-connect/VPN to the venue)",
        "credentials_required": [],
        "engine_support": "decoder_implemented",
        "default_config": {
            "provider": "NASDAQ", "feed": "TOTALVIEW_ITCH", "environment": "production", "enabled": False,
            "transport": {
                "type": "udp_multicast", "interface": "REPLACE_WITH_YOUR_NIC_IP",
                "group": "REPLACE_WITH_NASDAQ_MULTICAST_GROUP", "port": "REPLACE_WITH_PORT",
            },
            "protocol": {"type": "moldudp64"},
            "venue_adapter": "nasdaq_itch",
            "recovery": {"enabled": True},
            "ab_feed_enabled": True,
            "field_mapping": {},
        },
    },
    {
        "provider": "CME", "feed": "MDP3", "display_name": "CME Market Data Platform 3.0",
        "tier": "licensed", "protocol_family": "SBE",
        "coverage": "futures/options - MBO and MBP depth",
        "requires": "a CME market-data agreement, entitlements, multicast connectivity, and CME's own current SBE schema "
                    "(the engine ships an illustrative example schema, not CME's real one - see "
                    "data-plane/feedhandler-cpp/protocols/sbe/SbeSchema.hpp)",
        "credentials_required": [],
        "engine_support": "decoder_implemented",
        "default_config": {
            "provider": "CME", "feed": "MDP3", "environment": "production", "enabled": False,
            "transport": {
                "type": "udp_multicast", "interface": "REPLACE_WITH_YOUR_NIC_IP",
                "group": "REPLACE_WITH_CME_MULTICAST_GROUP", "port": "REPLACE_WITH_PORT",
            },
            "protocol": {"type": "sbe"},
            "venue_adapter": "cme_mdp3",
            "recovery": {"enabled": True},
            "ab_feed_enabled": True,
            "field_mapping": {},
        },
    },
    {
        "provider": "GENERIC_FIX", "feed": "MARKET_DATA", "display_name": "Generic FIX market data",
        "tier": "licensed", "protocol_family": "FIX",
        "coverage": "any venue/vendor speaking standard FIX market-data messages "
                    "(MarketDataSnapshotFullRefresh / MarketDataIncrementalRefresh)",
        "requires": "a FIX session agreement with the venue/vendor (SenderCompID/TargetCompID, host/port, "
                    "and typically a username/password for the Logon message)",
        "credentials_required": ["username", "password"],
        "engine_support": "decoder_implemented",
        "default_config": {
            "provider": "GENERIC_FIX", "feed": "MARKET_DATA", "environment": "production", "enabled": False,
            "transport": {"type": "tcp", "host": "REPLACE_WITH_HOST", "port": "REPLACE_WITH_PORT"},
            "protocol": {"type": "fix"},
            "venue_adapter": "generic_fix",
            "recovery": {"enabled": False},
            "field_mapping": {"sender_comp_id": "REPLACE_ME", "target_comp_id": "REPLACE_ME"},
        },
    },

    # ---- Generic protocol-family entries (any venue with a matching wire
    # format - not tied to one exchange, same spirit as GENERIC_FIX above) --
    _implemented(
        "GENERIC_ITCH_MOLDUDP64", "MARKET_DATA", "Generic ITCH-style market data (MoldUDP64/SoupBinTCP)",
        "licensed", "MoldUDP64/SoupBinTCP + ITCH-shaped messages",
        "any venue publishing ITCH-shaped order/trade messages over MoldUDP64 or SoupBinTCP framing",
        "that venue's own ITCH-derivative spec to confirm message type codes and field layout match "
        "(the engine's ITCH message parser implements NASDAQ TotalView-ITCH 5.0 - close relatives may "
        "need field-level verification before going live)",
        [],
        {"type": "udp_multicast", "interface": "REPLACE_WITH_YOUR_NIC_IP",
         "group": "REPLACE_WITH_MULTICAST_GROUP", "port": "REPLACE_WITH_PORT"},
        {"type": "moldudp64"}, "itch_style",
    ),
    _implemented(
        "GENERIC_SBE", "MARKET_DATA", "Generic SBE market data",
        "licensed", "SBE (Simple Binary Encoding)",
        "any venue publishing Simple Binary Encoding market data over UDP multicast or TCP",
        "that venue's own real SBE schema (message templates/field definitions) loaded in place of "
        "the engine's illustrative example schema - see protocols/sbe/SbeSchema.hpp",
        [],
        {"type": "udp_multicast", "interface": "REPLACE_WITH_YOUR_NIC_IP",
         "group": "REPLACE_WITH_MULTICAST_GROUP", "port": "REPLACE_WITH_PORT"},
        {"type": "sbe"}, "sbe_generic",
    ),
    _implemented(
        "GENERIC_WSJSON", "MARKET_DATA", "Generic WebSocket + JSON market data",
        "public", "WebSocket + JSON",
        "any venue/vendor streaming tick data as JSON messages over a WebSocket (no TLS support yet - "
        "plaintext ws:// only)",
        "the venue's real WebSocket URL, subscribe message, and JSON field names in place of the "
        "illustrative field_mapping shown here",
        [],
        {"type": "websocket", "host": "REPLACE_WITH_HOST", "port": "443", "path": "/",
         "subscribe_message": "REPLACE_WITH_SUBSCRIBE_MESSAGE"},
        {"type": "wsjson"}, "generic_wsjson",
        recovery_enabled=False,
        field_mapping={"filter_field": "type", "filter_value": "trade",
                       "field_symbol": "symbol", "field_price": "price", "field_qty": "size",
                       "field_side": "side", "side_buy_value": "buy"},
    ),

    # ---- US equities -----------------------------------------------------
    _future("NYSE", "PILLAR", "NYSE (Pillar)", "NYSE Pillar (proprietary binary)",
            "NYSE primary-listed US equities - full depth via Integrated Feed",
            "a NYSE Pillar binary decoder (own message framing, not ITCH or SBE - not yet implemented) "
            "plus a NYSE market-data agreement and cross-connect"),
    _future("NYSE_ARCA", "PILLAR", "NYSE Arca (Pillar)", "NYSE Pillar (proprietary binary)",
            "NYSE Arca-listed US equities and ETFs",
            "a NYSE Pillar binary decoder (not yet implemented) plus a NYSE Arca market-data agreement"),
    _future("NYSE_AMERICAN", "PILLAR", "NYSE American (Pillar)", "NYSE Pillar (proprietary binary)",
            "NYSE American-listed US equities and options",
            "a NYSE Pillar binary decoder (not yet implemented) plus an NYSE American market-data agreement"),
    _future("CBOE_US", "PITCH", "Cboe US Equities (BZX/BYX/EDGX/EDGA)", "Cboe PITCH (proprietary binary)",
            "US equities across Cboe's four exchanges - depth-of-book",
            "a Cboe PITCH binary decoder (own multicast framing and message set, not yet implemented) "
            "plus a Cboe market-data agreement"),
    _future("IEX", "TOPS_DEEP", "IEX (TOPS / DEEP)", "IEX proprietary binary",
            "IEX-listed and IEX-traded US equities - top-of-book (TOPS) or full depth (DEEP)",
            "an IEX TOPS/DEEP binary decoder (publicly documented by IEX but not yet implemented in this "
            "engine) - IEX itself requires no fee for its market data"),
    _future("MEMX", "MEMOIR", "MEMX (Members Exchange)", "MEMX MEMOIR (proprietary binary)",
            "MEMX-listed US equities",
            "a MEMX MEMOIR binary decoder (not yet implemented) plus a MEMX market-data agreement"),
    _future("MIAX", "BINARY_MD", "MIAX (Pearl/Emerald equities, MIAX options)", "MIAX proprietary binary",
            "MIAX-listed US equities and options",
            "a MIAX binary market-data decoder (not yet implemented) plus a MIAX market-data agreement"),

    # ---- Futures/derivatives (CME Group shares MDP3/SBE infrastructure) --
    _implemented(
        "CBOT", "MDP3", "CBOT (CME Group)", "licensed", "SBE",
        "grains/interest-rate futures and options - MBO/MBP depth",
        "a CME Group market-data agreement covering CBOT, entitlements, multicast connectivity, and "
        "CBOT's current SBE schema (same MDP3 platform as CME - see CME entry's schema caveat)",
        [],
        {"type": "udp_multicast", "interface": "REPLACE_WITH_YOUR_NIC_IP",
         "group": "REPLACE_WITH_CBOT_MULTICAST_GROUP", "port": "REPLACE_WITH_PORT"},
        {"type": "sbe"}, "cme_mdp3",
    ),
    _implemented(
        "NYMEX", "MDP3", "NYMEX (CME Group)", "licensed", "SBE",
        "energy futures and options - MBO/MBP depth",
        "a CME Group market-data agreement covering NYMEX, entitlements, multicast connectivity, and "
        "NYMEX's current SBE schema (same MDP3 platform as CME)",
        [],
        {"type": "udp_multicast", "interface": "REPLACE_WITH_YOUR_NIC_IP",
         "group": "REPLACE_WITH_NYMEX_MULTICAST_GROUP", "port": "REPLACE_WITH_PORT"},
        {"type": "sbe"}, "cme_mdp3",
    ),
    _implemented(
        "COMEX", "MDP3", "COMEX (CME Group)", "licensed", "SBE",
        "metals futures and options - MBO/MBP depth",
        "a CME Group market-data agreement covering COMEX, entitlements, multicast connectivity, and "
        "COMEX's current SBE schema (same MDP3 platform as CME)",
        [],
        {"type": "udp_multicast", "interface": "REPLACE_WITH_YOUR_NIC_IP",
         "group": "REPLACE_WITH_COMEX_MULTICAST_GROUP", "port": "REPLACE_WITH_PORT"},
        {"type": "sbe"}, "cme_mdp3",
    ),
    _future("ICE", "IMPACT", "ICE (iMpact market data)", "ICE proprietary binary",
            "energy/agricultural/financial futures and options across ICE's exchanges",
            "an ICE iMpact binary decoder (not yet implemented) plus an ICE market-data agreement - "
            "ICE also offers FIX drop-copy for orders (see GENERIC_FIX) but not full market-data depth"),
    _implemented(
        "EUREX", "EOBI", "Eurex (EOBI)", "licensed", "SBE",
        "European derivatives (equity/index/interest-rate futures and options) - full order book",
        "a Eurex market-data agreement, entitlements, connectivity, and Eurex's current EOBI SBE schema "
        "(the engine ships an illustrative example schema, not Eurex's real one)",
        [],
        {"type": "udp_multicast", "interface": "REPLACE_WITH_YOUR_NIC_IP",
         "group": "REPLACE_WITH_EUREX_MULTICAST_GROUP", "port": "REPLACE_WITH_PORT"},
        {"type": "sbe"}, "sbe_generic",
    ),
    _future("EUREX_FIX_FAST", "MARKET_DATA", "Eurex FIX/FAST market data (legacy)", "FIX/FAST",
            "Eurex market data over the older FIX/FAST delivery (superseded by EOBI for most use cases)",
            "a FAST protocol decoder - deliberately not implemented (needs an XML-template-driven "
            "compression codec, see module docstring); use the EUREX/EOBI entry instead where possible"),

    # ---- Europe cash equities --------------------------------------------
    _future("LSE", "MILLENNIUM_MITCH", "London Stock Exchange (Millennium Exchange)",
            "MITCH (ITCH-derived binary)",
            "LSE-listed UK/international equities - full order book",
            "an LSE MITCH decoder (structurally related to ITCH but with its own message set - not yet "
            "implemented/verified against LSE's real spec) plus an LSE market-data agreement"),
    _future("EURONEXT", "OPTIQ", "Euronext (Optiq)", "Euronext Optiq (proprietary binary)",
            "Euronext-listed equities across Amsterdam/Brussels/Dublin/Lisbon/Milan/Oslo/Paris",
            "a Euronext Optiq binary decoder (not yet implemented) plus a Euronext market-data agreement"),
    _implemented(
        "DEUTSCHE_BOERSE", "T7_EOBI", "Deutsche Börse / Xetra (T7 EOBI)", "licensed", "SBE",
        "Xetra-listed German/European equities - full order book (same T7 platform family as Eurex)",
        "a Deutsche Börse market-data agreement, entitlements, connectivity, and Xetra's current EOBI "
        "SBE schema (the engine ships an illustrative example schema, not Deutsche Börse's real one)",
        [],
        {"type": "udp_multicast", "interface": "REPLACE_WITH_YOUR_NIC_IP",
         "group": "REPLACE_WITH_XETRA_MULTICAST_GROUP", "port": "REPLACE_WITH_PORT"},
        {"type": "sbe"}, "sbe_generic",
    ),
    _future("SIX", "MD", "SIX Swiss Exchange", "SIX proprietary binary",
            "Swiss-listed equities and structured products",
            "a SIX market-data binary decoder (not yet implemented) plus a SIX market-data agreement"),
    _future("CBOE_EUROPE", "BOE_PITCH", "Cboe Europe", "Cboe PITCH/BOE (proprietary binary)",
            "Pan-European equities across Cboe's European books",
            "a Cboe Europe PITCH/BOE binary decoder (not yet implemented) plus a Cboe Europe market-data agreement"),
    _future("TURQUOISE", "MILLENNIUM_MITCH", "Turquoise (LSE Group)", "MITCH (ITCH-derived binary)",
            "Turquoise pan-European equities and Turquoise Plato dark trading",
            "same MITCH decoder gap as LSE (not yet implemented) plus a Turquoise market-data agreement"),
    _future("BORSA_ITALIANA", "MILLENNIUM_MITCH", "Borsa Italiana (LSE Group)", "MITCH (ITCH-derived binary)",
            "Italian-listed equities on the Millennium Exchange platform (same LSE Group technology as LSE/Turquoise)",
            "same MITCH decoder gap as LSE (not yet implemented) plus a Borsa Italiana market-data agreement"),

    # ---- Turkey/MENA (genuinely ITCH/OUCH per the venue's own public docs) -
    _implemented(
        "BORSA_ISTANBUL", "ITCH_OUCH", "Borsa Istanbul", "licensed", "MoldUDP64/SoupBinTCP + ITCH-shaped messages",
        "Turkish-listed equities and derivatives - Borsa Istanbul publishes market data as ITCH-shaped "
        "messages over MoldUDP64 and order entry as OUCH over SoupBinTCP, per its own public technical "
        "resources (referenced in this engine's SoupBinTcpDecoder.hpp)",
        "a Borsa Istanbul market-data agreement, entitlements, and connectivity - field-level "
        "verification against Borsa Istanbul's own current ITCH spec version before going live",
        [],
        {"type": "udp_multicast", "interface": "REPLACE_WITH_YOUR_NIC_IP",
         "group": "REPLACE_WITH_BORSA_ISTANBUL_MULTICAST_GROUP", "port": "REPLACE_WITH_PORT"},
        {"type": "moldudp64"}, "itch_style",
    ),

    # ---- Asia-Pacific ------------------------------------------------------
    _future("JPX", "FLEX_FULL", "Japan Exchange Group (JPX FLEX Full)", "JPX FLEX (proprietary binary)",
            "Tokyo Stock Exchange-listed equities and derivatives - full order book",
            "a JPX FLEX Full binary decoder (not yet implemented) plus a JPX market-data agreement"),
    _future("HKEX", "OMD_C", "Hong Kong Exchanges (OMD-C)", "HKEX OMD-C (proprietary binary)",
            "HKEX-listed cash equities - Orion Market Data platform",
            "an HKEX OMD-C binary decoder (not yet implemented/verified against HKEX's real spec) plus "
            "an HKEX market-data agreement"),
    _future("SGX", "MD", "Singapore Exchange", "SGX proprietary binary",
            "SGX-listed equities and derivatives",
            "an SGX market-data binary decoder (not yet implemented/verified against SGX's real spec) "
            "plus an SGX market-data agreement"),
    _implemented(
        "ASX", "ASX_TRADE_ITCH", "Australian Securities Exchange (ASX Trade)",
        "licensed", "MoldUDP64/SoupBinTCP + ITCH-shaped messages",
        "ASX-listed equities - ASX Trade is explicitly licensed from NASDAQ's ITCH technology",
        "an ASX market-data agreement, entitlements, and connectivity - field-level verification "
        "against ASX's own current ITCH spec version before going live",
        [],
        {"type": "udp_multicast", "interface": "REPLACE_WITH_YOUR_NIC_IP",
         "group": "REPLACE_WITH_ASX_MULTICAST_GROUP", "port": "REPLACE_WITH_PORT"},
        {"type": "moldudp64"}, "itch_style",
    ),
    _future("KRX", "MD", "Korea Exchange", "KRX proprietary binary",
            "KRX-listed equities and derivatives",
            "a KRX market-data binary decoder (not yet implemented) plus a KRX market-data agreement"),
    _future("B3", "UMDF", "B3 (Brazil)", "B3 UMDF (Unified Market Data Feed, proprietary binary)",
            "B3-listed Brazilian equities and derivatives",
            "a B3 UMDF binary decoder (not yet implemented) plus a B3 market-data agreement"),

    # ---- Data vendors (aggregate multiple venues behind one connection) ---
    _future("LSEG_REFINITIV_ELEKTRON", "RSSL", "LSEG/Refinitiv (Elektron/RSSL)",
            "RSSL (proprietary vendor protocol)",
            "consolidated global market data via LSEG's Elektron/Real-Time RSSL transport",
            "LSEG's own vendor SDK/API - RSSL is not an independently-implementable public wire spec the "
            "way ITCH/SBE/FIX are (same category of constraint as KX's own C API, which this repo also "
            "deliberately doesn't bundle), so this needs LSEG's licensed client library, not a from-scratch "
            "decoder", ["username", "password"]),
    _implemented(
        "LSEG_REFINITIV_RDP", "WS_JSON", "LSEG/Refinitiv Data Platform (WebSocket)",
        "licensed", "WebSocket + JSON",
        "consolidated global market data via LSEG's newer Refinitiv Data Platform streaming WebSocket API",
        "an RDP account/API credentials, and real field_mapping matching RDP's actual JSON message "
        "schema in place of the illustrative one shown here",
        ["api_key", "username", "password"],
        {"type": "websocket", "host": "REPLACE_WITH_RDP_WS_HOST", "port": "443", "path": "/WebSocket",
         "subscribe_message": "REPLACE_WITH_RDP_LOGIN_AND_SUBSCRIBE_MESSAGE"},
        {"type": "wsjson"}, "generic_wsjson",
        recovery_enabled=False,
        field_mapping={"filter_field": "Type", "filter_value": "Update",
                       "field_symbol": "Key.Name", "field_price": "Fields.TRDPRC_1",
                       "field_qty": "Fields.TRDVOL_1", "field_side": "", "side_buy_value": ""},
    ),
    _future("ICE_DATA_SERVICES", "CONSOLIDATED_FEED", "ICE Data Services (consolidated feed)",
            "ICE proprietary binary / vendor SDK",
            "consolidated global market data via ICE Data Services",
            "ICE Data Services' own vendor connectivity/SDK for their consolidated feed (not yet "
            "implemented) - their FIX order/drop-copy connectivity, where offered, can use GENERIC_FIX instead",
            ["username", "password"]),
    _implemented(
        "SP_GLOBAL", "WS_JSON", "S&P Global Market Intelligence", "licensed", "WebSocket + JSON",
        "reference and real-time market data via S&P Global's streaming API products",
        "an S&P Global Market Intelligence subscription/API credentials, and real field_mapping "
        "matching their actual JSON message schema in place of the illustrative one shown here",
        ["api_key"],
        {"type": "websocket", "host": "REPLACE_WITH_HOST", "port": "443", "path": "/",
         "subscribe_message": "REPLACE_WITH_SUBSCRIBE_MESSAGE"},
        {"type": "wsjson"}, "generic_wsjson",
        recovery_enabled=False,
        field_mapping={"filter_field": "type", "filter_value": "trade",
                       "field_symbol": "symbol", "field_price": "price", "field_qty": "size",
                       "field_side": "side", "side_buy_value": "buy"},
    ),
    _implemented(
        "FACTSET", "WS_JSON", "FactSet", "licensed", "WebSocket + JSON",
        "reference and real-time market data via FactSet's streaming API",
        "a FactSet subscription/API credentials, and real field_mapping matching FactSet's actual JSON "
        "message schema in place of the illustrative one shown here",
        ["api_key"],
        {"type": "websocket", "host": "REPLACE_WITH_HOST", "port": "443", "path": "/",
         "subscribe_message": "REPLACE_WITH_SUBSCRIBE_MESSAGE"},
        {"type": "wsjson"}, "generic_wsjson",
        recovery_enabled=False,
        field_mapping={"filter_field": "type", "filter_value": "trade",
                       "field_symbol": "symbol", "field_price": "price", "field_qty": "size",
                       "field_side": "side", "side_buy_value": "buy"},
    ),

    # ---- Colocation / direct multicast (deployment mode, not a protocol) -
    # Colocation doesn't change the wire protocol - it changes WHERE the
    # process listening for it runs (inside the venue's own datacenter, on
    # a cross-connect, for minimum latency). Every udp_multicast entry above
    # (NASDAQ/CME/CBOT/NYMEX/COMEX/EUREX/DEUTSCHE_BOERSE/BORSA_ISTANBUL/ASX)
    # already uses the same transport a colocated deployment would use -
    # `transport.interface` just needs to point at the colo NIC instead of a
    # VPN/cross-connect endpoint. There's no separate "colo protocol" to add.
]


def find(provider: str, feed: str) -> dict | None:
    for entry in FEEDHANDLER_CATALOG:
        if entry["provider"] == provider and entry["feed"] == feed:
            return entry
    return None
