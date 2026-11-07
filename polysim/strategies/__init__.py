from .examples import BuyAndHold, MeanReversion, SimpleMarketMaker

REGISTRY = {
    BuyAndHold.name: BuyAndHold,
    MeanReversion.name: MeanReversion,
    SimpleMarketMaker.name: SimpleMarketMaker,
}

__all__ = ["REGISTRY", "BuyAndHold", "MeanReversion", "SimpleMarketMaker"]
