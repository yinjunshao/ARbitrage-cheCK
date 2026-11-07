"""arbcheck: a false-arbitrage checker for prediction markets.

Pipeline (each step is its own module):

    polymarket.py  ->  relations.py  ->  engine.py  ->  guard.py  ->  report.py
    fetch markets      LLM reads the     pure maths:     risk limits    plain-English
    and order books    rules, names the  payoffs, book   + decision     decision cards
                       logical link and  walking, fees   log
                       rule mismatches

The LLM only *interprets text*. All numbers come from deterministic code, and the LLM
can only make a trade less likely (veto / haircut), never more likely.
"""

__version__ = "0.1.0"
