import logging

logger = logging.getLogger("ax.macro")

# Eşik değerler: Binance varsayılan fonlama oranı %0.0100 civarıdır.
# Eğer 24 saatlik ortalama %0.04 ve üzeriyse bu çılgın bir yükseliş beklentisidir (Squeeze riski)
FUNDING_EXTREME_GREED = 0.0004  # 0.04%
FUNDING_EXTREME_FEAR = -0.0004  # -0.04%

class MacroFilter:
    """
    On-chain ve Funding Rate gibi makro verileri analiz edip
    trend yönünde aşırı şişme (Squeeze tehlikesi) olup olmadığını tespit eder.
    """
    def __init__(self, client):
        self.client = client
        self._funding_cache = {}

    def get_24h_funding_trend(self, symbol: str) -> dict:
        """
        Son 3 fonlama oranını (3x8 = 24 saat) çeker ve ortalamasını alır.
        """
        try:
            # Önce cache kontrolü yapabiliriz ama real-time sistemlerde cache 
            # süresi yönetmek zor, şimdilik doğrudan soralım veya basit bir in-memory yapalım.
            # Şimdilik doğrudan Binance'e soruyoruz.
            
            result = self.client.futures_funding_rate(symbol=symbol, limit=3)
            
            if not result or len(result) == 0:
                return {"bias": "NEUTRAL", "avg_rate": 0.0}

            rates = [float(r["fundingRate"]) for r in result]
            avg_rate = sum(rates) / len(rates)
            
            bias = "NEUTRAL"
            if avg_rate >= FUNDING_EXTREME_GREED:
                bias = "EXTREME_GREED" # Longlar çok şişmiş, long açma!
            elif avg_rate <= FUNDING_EXTREME_FEAR:
                bias = "EXTREME_FEAR"  # Shortlar çok şişmiş, short açma!
                
            return {
                "bias": bias,
                "avg_rate": avg_rate,
                "rates": rates
            }
        except Exception as e:
            logger.debug(f"[MacroFilter] {symbol} funding rate çekilirken hata: {e}")
            return {"bias": "NEUTRAL", "avg_rate": 0.0}

