import os
import sys
import time
import logging
import argparse
import threading
import signal as signal_module
from datetime import datetime
from typing import Dict, Optional, List


# ── Path setup ────────────────────────────────────────────────────────────────
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
    sys.path.insert(0, sys._MEIPASS)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

os.environ.setdefault('ENV_FILE', os.path.join(BASE_DIR, '.env'))

# ── Logging setup ─────────────────────────────────────────────────────────────

class TruncateFilter(logging.Filter):
    def filter(self, record):
        record.name = record.name[:15]
        return True

_formatter = logging.Formatter(
    fmt='%(asctime)s %(name)-8s %(levelname)-5s %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
_console = logging.StreamHandler()
_console.setLevel(logging.INFO)
_console.setFormatter(_formatter)

_file_handler = logging.FileHandler(
    "system.log",
    mode='w',
    encoding="utf-8",
)

_file_handler.setLevel(logging.DEBUG)
_file_handler.setFormatter(_formatter)

logging.basicConfig(level=logging.DEBUG, handlers=[_console, _file_handler])
for _h in logging.root.handlers:
    _h.addFilter(TruncateFilter())

logger = logging.getLogger("MT4IQ Signal Bridge Trader")



from iqoptionapi.iqapi import IQOptionClient
from iqoptionapi.candles import Candle
from iqoptionapi.models import Direction, OptionsTradeParams

from tradingconfig import TradingConfig, AnalyticsConfig, PairConfig
from bot.analytics.analyzer import TradeAnalyzer
from bot.strategies.bar_by_bar import bar_by_bar_signal
from bot.helpers import wait_for_minute_start
from tutorial2 import RiskManager


def generate_signal_from_live_candle(candle) -> Direction:
    """
    Generate trading signal from a single completed live candle.
    Replace this with your actual strategy logic.
    """
    if candle.close > candle.open:
        return Direction.CALL
    elif candle.close < candle.open:
        return Direction.PUT
    else:
        return Direction.INDECISION


class TradingBot:
    def __init__(self):
        self.config          = TradingConfig()
        self.analytics_config = AnalyticsConfig()
        self.risk_manager    = RiskManager(self.config)
        self.analyzer        = TradeAnalyzer(self.analytics_config)
        self.client          = None
        self.latest_candle   = None
        logger.info("🤖 TradingBot initialized")

    def connect(self) -> bool:
        logger.info("🔌 Connecting to IQOption...")
        try:
            self.client = IQOptionClient()
            self.client.connect()
            if self.client._connected:
                balance = self.client.get_balance()
                self.risk_manager.update_balance(balance)
                self.risk_manager.starting_balance = balance
                logger.info(f"✅ Connected! Balance: ${balance:.2f}")
                return True
        except Exception as e:
            logger.error(f"❌ Connection failed: {e}")
        return False

    def on_new_candle_signal(self, candle):
        """Callback when a new candle closes"""
        self.latest_candle = candle
        
        logger.debug(f"📊 New candle: O={candle.open:.5f}, H={candle.high:.5f}, "
                    f"L={candle.low:.5f}, C={candle.close:.5f}")
        
        signal = generate_signal_from_live_candle(candle)
        
        if signal != Direction.INDECISION:
            logger.info(f"🎯 Signal generated: {signal.value}")
            # ✅ Fixed: args=(signal,) with comma
            threading.Thread(
                target=self.execute_trade,
                args=(signal,),  # ← COMMA IS IMPORTANT!
                daemon=True,
            ).start()


    def on_new_candle_signal(self, candle: Candle, history: List[Candle]) -> None:
        """WebSocket thread — must return fast. detect() is pure arithmetic."""
        # print(history)

        pair = self.config.get_pair(candle.asset_name)
        if not pair:
            return

        signal = generate_signal_from_live_candle(history[-1])
        if signal == Direction.INDECISION:
            return

        threading.Thread(
            target=self.execute_trade,
            args=(pair, signal),
            daemon=True,
        ).start()

    def log_signals(self, log, direction, asset_name):
        arrow = "🟢 CALL ↑" if direction == Direction.CALL else "🔴 PUT ↓"
        # print()
        if log  == logger.debug:
            logger.debug("━" * 50)
            logger.debug("🎯 SIGNAL  %-12s %s", asset_name, arrow)
            logger.debug("━" * 50)
        else:
            logger.info("━" * 50)
            logger.info("🎯 SIGNAL  %-12s %s", asset_name, arrow)
            logger.info("━" * 50)

    def execute_trade(self, pair:PairConfig, direction: Direction) -> Optional[Dict]:
        position_size  = self.risk_manager.calculate_position_size()
        balance_before = self.risk_manager.current_balance
        
        logger.info(f"💰 Executing {direction.value} trade: ${position_size:.2f}")

        success, order_id = self.client.execute_options_trade(OptionsTradeParams(
            asset=pair.asset,
            expiry=pair.expiry,
            amount=position_size,
            direction=direction,
            option_type=pair.option_type,
        ))

        if not success or not order_id:
            logger.error(f"❌ Trade failed: {order_id}")
            return None

        success, outcome_data, pnl = self.client.get_trade_outcome(
            order_id, pair.expiry
        )

        if success and outcome_data is not None:
            balance_after = balance_before + pnl
            self.risk_manager.record_trade(pnl)
            
            trade_data = {
                'trade_id':       order_id,
                'timestamp':      datetime.now().isoformat(),
                'asset':          pair.asset,
                'direction':      direction.value,
                'amount':         position_size,
                'expiry_minutes': pair.expiry,
                'pnl':            pnl,
                'balance_before': balance_before,
                'balance_after':  balance_after,
                'outcome':        'win' if pnl > 0 else 'loss' if pnl < 0 else 'draw',
            }
            
            self.analyzer.add_trade(trade_data)
            
            # Log the result clearly
            if pnl > 0:
                logger.info(f"✅ WIN! +${pnl:.2f} | Balance: ${balance_after:.2f}")
            elif pnl < 0:
                logger.info(f"❌ LOSS! ${pnl:.2f} | Balance: ${balance_after:.2f}")
            else:
                logger.info(f"⚪ DRAW | Balance: ${balance_after:.2f}")
            
            return trade_data
        
        logger.error(f"❌ Failed to get trade outcome for {order_id}")
        return None

    def run(self):
        if not self.connect():
            logger.error("❌ Failed to connect. Exiting...")
            return

        logger.info("✅ Connected successfully!")
        logger.info(f"   📊 Account : {self.client.appstate.balance_type_str.capitalize()}")
        logger.info(f"   💵 Balance : ${self.risk_manager.starting_balance:.2f}")
        logger.info(f"   📁 Output  : {self.analytics_config.output_dir}/")
        self.config.display()

        if not self.client.subscribe_live_candles(self.config.pairs):
            logger.error("❌ Failed to subscribe to live candles. Exiting...")
            return
        
        self.client.on_new_candle(self.on_new_candle_signal)
        
        logger.info("✅ Bot running with LIVE candles | Press Ctrl+C to stop")
        logger.info("=" * 60)

        try:
            while True:
                can_trade, reason = self.risk_manager.can_trade()
                if not can_trade:
                    logger.warning(f"⛔ Trading blocked: {reason}")
                    self.risk_manager.print_status()
                    if "profit target" in reason or "loss limit" in reason:
                        logger.info("🏁 Daily limits reached. Generating final report...")
                        break
                    time.sleep(30)
                    continue
                
                # Keep the bot alive - candles come via callback
                time.sleep(1)

        except KeyboardInterrupt:
            logger.info("\n\n🛑 Trading stopped by user")
        except Exception as e:
            logger.error(f"💥 Unexpected error: {e}", exc_info=True)
        finally:
            self._generate_report()
            if self.client:
                self.client.disconnect()
                logger.info("🔌 Disconnected")

    def _generate_report(self):
        logger.info("📊 Generating performance report...")
        print("\n" + "=" * 60)
        print("📊 GENERATING PERFORMANCE REPORT")
        print("=" * 60)
        
        self.analyzer.to_dataframe()
        metrics = self.analyzer.calculate_metrics()
        self.analyzer.print_performance_report(metrics)
        
        if self.analytics_config.save_csv:
            self.analyzer.save_csv()
            logger.info(f"📁 CSV saved to {self.analytics_config.output_dir}/")
        
        self.analyzer.save_master_stats(metrics)
        
        if self.analytics_config.generate_charts:
            self.analyzer.generate_charts()
            logger.info("📊 Charts generated")
        
        print("\n✅ Report generation complete!")
        print(f"📁 Output folder: '{self.analytics_config.output_dir}/'")
        print("=" * 60)
        
        logger.info("📊 Report generation complete")


if __name__ == "__main__":        
    try:
        TradingBot().run()
    except Exception as e:
        logger.error(f"💥 Fatal error: {e}", exc_info=True)
    finally:
        logger.info("=" * 60)
        logger.info("🏁 Trading Bot stopped")
        logger.info("=" * 60)