"""Shared constants and builders for the polysim tests.

Kept out of conftest.py on purpose: other packages in this repo (tests/frame) have their own
conftest.py, and `from conftest import ...` resolves to whichever one is first on sys.path.
"""
from polysim import OrderBook, PriceLevel

YES, NO = "T-YES", "T-NO"


def make_book(ts=0.0, bids=((0.48, 100), (0.47, 200)), asks=((0.52, 100), (0.53, 200)), token=YES):
    return OrderBook(token, ts, [PriceLevel(p, s) for p, s in bids], [PriceLevel(p, s) for p, s in asks])
