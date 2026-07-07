import time
import logging
from datetime import datetime
from typing import Dict, Optional

from iqoptionapi.iqapi import IQOptionClient
from iqoptionapi.models import Direction, OptionsTradeParams

from tradingconfig import TradingConfig, AnalyticsConfig
from bot.analytics.analyzer import TradeAnalyzer
from bot.strategies.bar_by_bar import bar_by_bar_signal
from bot.helpers import wait_for_minute_start
from tutorial2 import RiskManager

logger = logging.getLogger("AutoTrading Bot")




def generate_signal_from_live_candle(candle) -> Direction:
    """
    Generate trading signal from a single completed live candle.
    Replace this with your actual strategy logic.
    """
    # Example simple strategy:
    # If candle closes higher than it opened → CALL
    # If closes lower than opened → PUT
    
    if candle.close > candle.open:
        return Direction.CALL
    elif candle.close < candle.open:
        return Direction.PUT
    else:
        return Direction.INDECISION
    
    # You can also access:
    # candle.high, candle.low, candle.volume, candle.timestamp



class TradingBot:
    def __init__(self):
        self.config          = TradingConfig()
        self.analytics_config = AnalyticsConfig()
        self.risk_manager    = RiskManager(self.config)
        self.analyzer        = TradeAnalyzer(self.analytics_config)
        self.client          = None

    def connect(self) -> bool:
        logger.info("🔌 Connecting to IQOption...")
        try:
            self.client = IQOptionClient()
            self.client.connect()
            if self.client._connected:
                balance = self.client.get_balance()
                self.risk_manager.update_balance(balance)
                self.risk_manager.starting_balance = balance
                return True
        except Exception as e:
            logger.error(f"Connection failed: {e}")
        return False

    # Add this method to TradingBot class
    def subscribe_to_live_candles(self) -> bool:
        """Subscribe to real-time candle updates"""
        logger.info(f"📡 Subscribing to live candles for {self.config.asset}...")
        
        # Subscribe to 1-minute candles (or use self.config.expiry_minutes * 60 for timeframe)
        success = self.client.start_candle_stream(
            asset=self.config.asset,
            candle_size=60  # 1-minute candles
        )
        
        if success:
            # Register callback for new candles
            self.client.on_new_candle(self.on_new_candle_signal)
            logger.info("✅ Live candle subscription active")
        return success

    def on_new_candle_signal(self, candle):
        """
        Callback when a new candle closes.
        This is where you generate signals from LIVE data.
        """
        # Store the candle for your strategy
        self.latest_candle = candle
        
        # Your signal logic here
        signal = generate_signal_from_live_candle(candle)
        
        if signal != Direction.INDECISION:
            logger.info(f"🎯 Signal generated from live candle: {signal.value}")
            self.execute_trade(signal)

    def execute_trade(self, direction: Direction) -> Optional[Dict]:
        position_size  = self.risk_manager.calculate_position_size()
        balance_before = self.risk_manager.current_balance

        success, order_id = self.client.execute_options_trade(OptionsTradeParams(
            asset=self.config.asset,
            expiry=self.config.expiry_minutes,
            amount=position_size,
            direction=direction,
            option_type=self.config.option_type,
        ))

        if not success or not order_id:
            logger.error(f"❌ Trade failed: {order_id}")
            return None

        success, outcome_data, pnl = self.client.get_trade_outcome(
            order_id, self.config.expiry_minutes
        )

        if success and outcome_data is not None:
            balance_after = balance_before + pnl
            self.risk_manager.record_trade(pnl)
            trade_data = {
                'trade_id':       order_id,
                'timestamp':      datetime.now().isoformat(),
                'asset':          self.config.asset,
                'direction':      direction.value,
                'amount':         position_size,
                'expiry_minutes': self.config.expiry_minutes,
                'pnl':            pnl,
                'balance_before': balance_before,
                'balance_after':  balance_after,
                'outcome':        'win' if pnl > 0 else 'loss' if pnl < 0 else 'draw',
            }
            self.analyzer.add_trade(trade_data)
            logger.info(f"📊 Trade Result: ${pnl:+.2f} | New Balance: ${balance_after:.2f}")
            return trade_data
        return None

    def run(self):
        if not self.connect():
            logger.error("Failed to connect. Exiting...")
            return

        logger.info("✅ Connected successfully!")
        logger.info(f"   📊 Account : {self.client.appstate.balance_type_str.capitalize()}")
        logger.info(f"   💵 Balance : ${self.risk_manager.starting_balance:.2f}")
        logger.info(f"   📁 Output  : {self.analytics_config.output_dir}/")
        self.config.display()

        # NEW: Subscribe to live candles
        if not self.subscribe_to_live_candles():
            logger.error("Failed to subscribe to live candles. Exiting...")
            return
        
        logger.info("🤖 Bot running with LIVE candles | Press Ctrl+C to stop")
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

                # wait_for_minute_start(self.client)
                # signal = bar_by_bar_signal(self.client, self.config.asset)

                # if signal == Direction.INDECISION:
                #     time.sleep(55)
                #     continue

                # self.execute_trade(signal)
                # self.risk_manager.print_status()
                time.sleep(1)

        except KeyboardInterrupt:
            print("\n\n🛑 Trading stopped by user")
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
        finally:
            self._generate_report()
            self.client.disconnect()
            logger.info("🔌 Disconnected")

    def _generate_report(self):
        print("\n" + "=" * 60)
        print("📊 GENERATING PERFORMANCE REPORT")
        print("=" * 60)
        self.analyzer.to_dataframe()
        metrics = self.analyzer.calculate_metrics()
        self.analyzer.print_performance_report(metrics)
        if self.analytics_config.save_csv:
            self.analyzer.save_csv()
        self.analyzer.save_master_stats(metrics)
        if self.analytics_config.generate_charts:
            self.analyzer.generate_charts()
        print("\n✅ Report generation complete!")
        print(f"📁 Output folder: '{self.analytics_config.output_dir}/'")
        print("=" * 60)


if __name__ == "__main__":
    TradingBot().run()