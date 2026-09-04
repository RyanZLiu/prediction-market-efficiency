from pme.backtest import PaperBacktester, summarize_trades
from pme.database import Database


def main() -> None:
    db = Database()
    db.init()
    trades = PaperBacktester().run(db.opportunities())
    print(summarize_trades(trades))
    db.close()


if __name__ == "__main__":
    main()
