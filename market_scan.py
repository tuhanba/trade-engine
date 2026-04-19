from binance.client import Client
import os
from dotenv import load_dotenv
load_dotenv()
c=Client(os.getenv('BINANCE_API_KEY'),os.getenv('BINANCE_API_SECRET'))
tickers=c.get_ticker()
usdt=[t for t in tickers if t['symbol'].endswith('USDT')]
usdt.sort(key=lambda x:float(x['quoteVolume']),reverse=True)
print('Toplam coin:',len(usdt))
for t in usdt[:100]:
    chg=float(t['priceChangePercent'])
    vol=float(t['quoteVolume'])/1000000
    print(f"{t['symbol']:<15} Hacim:{vol:>8.1f}M Degisim:{chg:>+6.2f}%")
