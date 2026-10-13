"""SolPersona Gallery for Raspberry Pi — the local agent.

The agent is the half of the player that a browser cannot be: it holds the
screen's credential, talks to SolPersona's device API, keeps the gallery on
the SD card, and switches the panel on and off. The other half — the web app
in Chromium — only draws.
"""

__version__ = "0.1.0"
PLATFORM = "raspberry-pi"
