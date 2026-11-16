"""PCIM — the Personal Contextual Intelligence Model behind Personal CI.

Layer map (docs/02_PCIM_ARCHITECTURE.md):

    A  personal representation   profile.py, policy/learned.py (latent part)
    B  temporal context memory   memory.py, store.py
    C  intent and goal modeling  intent.py
    D  context selection policy  policy/
    E  cognitive adaptation      assist.py, growth.py
    F  foundation-model interface adapters/
    G  outcome evaluation        feedback.py, observatory/
    H  continual learning        learning.py

Context sovereignty (consent, egress, audit, crypto-shredding, injection gating) is
not a layer; it wraps every layer and lives in sovereignty/.
"""

__version__ = "0.2.0rc2"
