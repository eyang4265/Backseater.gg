"""Discord bot for League of Legends match tracking.

Layering (each layer may only import from the ones above it):

    config, runtime          process configuration and the Discord client handle
    routing, ranks            pure domain logic, no I/O
    riot, ddragon, store     I/O adapters (Riot API, Data Dragon CDN, JSON files)
    positions, emoji         derived lookups built on the adapters
    render, charts           presentation
    tracker                  background match and live-game pollers
    commands                 slash-command handlers

Keeping the dependency arrows one-directional is what removes the import
cycles the previous layout needed lazy in-function imports to work around.
"""
