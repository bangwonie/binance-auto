# BTC/USDT Futures Demo and Real dashboard

## Hai trang Futures

Khi chạy `start-futures-demo.ps1`, mở `http://127.0.0.1:3000/demo` cho bot Futures Demo và `http://127.0.0.1:3000/real` cho thị trường Futures Real. Trang Real hiện chỉ theo dõi, không có endpoint đặt lệnh thật. Hai trang dùng nguồn dữ liệu riêng: Demo từ `demo-fapi.binance.com` và Futures Real từ `fapi.binance.com`. Không dùng key Demo trên API Real.

Giá, nến 5 phút, sổ lệnh và best bid/ask của Real đi trực tiếp từ Binance Futures WebSocket tới trình duyệt. Giá hiển thị lấy từ luồng giao dịch gần nhất (`aggTrade`); nguồn sự kiện và độ trễ nhận được hiển thị trong thanh trên. Sau khi mất kết nối, socket tự nối lại và tải lại nến/sổ lệnh bằng REST. Khoảng trễ đo được chịu ảnh hưởng đồng hồ của máy và mạng, nên không thể cam kết trùng từng mili giây với giao diện Binance.

Trang Real có thể hiển thị tài khoản nếu khởi động bằng `powershell -File .\start-futures-demo.ps1 -WithRealAccount` và nhập thêm **API key Real chỉ đọc** tại lời nhắc cục bộ. Server dùng Futures user data stream để nhận sự kiện tài khoản/lệnh, làm mới ảnh chụp tài khoản sau sự kiện và đồng bộ REST mỗi phút. Key và secret chỉ ở bộ nhớ tiến trình; browser chỉ nhận số dư/vị thế đã lọc, không nhận key hoặc listenKey. Nếu chưa có key Real, trang Real vẫn hiển thị thị trường trực tiếp và đánh dấu phần tài khoản là chưa kết nối. Không dán key vào mã nguồn hoặc chat.

Để chủ động bật bot Real, dùng `powershell -File .\start-futures-demo.ps1 -EnableRealTrading`. Cờ này yêu cầu API key Real có quyền Futures trading và mặc định chạy long-only SMA20/50 trên nến 5 phút, isolated 1x, tối đa 60 USDT mỗi lần mở, giới hạn lỗ ngày 20 USDT và stop loss 2% bằng lệnh `STOP_MARKET` trên Binance. Có thể đổi giới hạn bằng `-RealMaxUsdt`, `-RealDailyLossUsdt`, `-RealStopLossPct`. Bot không mở lệnh nếu gặp vị thế BTCUSDT không do nó quản lý, hedge mode, lệnh chờ sẵn, dữ liệu tín hiệu quá cũ, chạm giới hạn lỗ ngày, hoặc không xác minh được trạng thái lệnh. Sau khi mở Long, nếu stop bảo vệ không thể được xác minh, bot thử đóng vị thế bằng lệnh reduce-only khẩn cấp và dừng báo lỗi. Trạng thái nằm trong `state-real.json`; không sửa hoặc xóa file đó khi còn vị thế.

Biểu đồ hiện là canvas tự vẽ từ nến Binance và cập nhật nến đang chạy theo luồng `kline`; không nhúng thư viện TradingView. Cách cập nhật nến tuân theo mô hình thay thế nến hiện tại bằng bản OHLCV mới. Trang Real hiển thị PnL chưa chốt và ROI vị thế từ tài khoản Futures khi có kết nối, không suy đoán PnL đã chốt.

## Binance Futures Demo

The Futures Demo mode uses `https://demo-fapi.binance.com` and a separate long-only BTCUSDT SMA20/50 bot. It checks closed 5-minute candles, enters a long position on an upward crossover and closes that managed position on a downward crossover. Before an entry it sets isolated margin and 1x leverage. Each entry targets at most 60 USDT notional because the Futures Demo BTCUSDT minimum is 50 USDT. It does not short. Orders use market execution and may slip. There is no stop loss; this is an unvalidated example strategy on virtual funds.

Stop the paper server, then run `powershell -File .\start-futures-demo.ps1` and enter the Binance Futures Demo API key and secret locally. The dashboard remains at http://127.0.0.1:3000 . The key must belong to Futures Demo API Management, not Spot Testnet. The bot refuses to place orders if the account is in hedge mode, already has an unmanaged BTCUSDT position or open BTCUSDT orders, or cannot verify credentials. Futures Demo state is kept in `state-futures_demo.json`, and PnL comparison starts from `baseline-futures_demo.json`.

Node.js 20+ and Python 3.10+ are required. No npm or pip packages are needed. The dashboard is **read-only**. Starting the Node server also starts the bot; keep that process running for automatic checks.

## Start in paper mode

```powershell
node server.mjs
```

Open http://127.0.0.1:3000 . Paper mode starts with 1,000 virtual USDT in `state-paper.json`. The bot checks immediately on startup, when the Binance 5-minute candle-close WebSocket event arrives, and once per minute as a fallback. It records each closed candle once, so the fallback does not repeat a trade.

## Start in Spot Testnet mode

Create Spot Testnet credentials at https://testnet.binance.vision/ . Set them in the same terminal before starting the server:

On Windows, you can run `powershell -File .\start-testnet.ps1` and enter the key and secret in masked prompts. The script keeps them in process memory and does not save them to disk. Stop the paper server first, since both modes use port 3000.

```powershell
$env:AUTO_MODE = 'testnet'
$env:BINANCE_TESTNET_API_KEY = 'your-testnet-key'
$env:BINANCE_TESTNET_API_SECRET = 'your-testnet-secret'
node server.mjs
```

Do not paste keys into chat or source files. This project is hardwired to Binance **Spot Testnet**, not mainnet. If Testnet keys are missing, the server shows a blocked status and does not run the bot. Run only one server process against the same state files. Closing the terminal stops automation; this prototype is not installed as an operating-system service.

## Strategy and monitoring

The example strategy buys BTC when the 20-period SMA crosses above the 50-period SMA and sells held BTC on the reverse cross. It uses **closed 5-minute candles**, not each incoming price tick. The maximum buy is 25 USDT per signal. The strategy is an example and has not been validated for profitability; it has no stop loss.

The dashboard streams public price, order book, and current candle data over WebSocket. Account balances and local history refresh every 5 seconds. Paper fills use a reference close and an illustrative 0.1% fee, without slippage. Testnet PnL/ROI use `baseline-testnet.json` as the initial value; after a Testnet reset or deposit, stop the server and remove that baseline file to start a new comparison.

`state-paper.json` and `state-testnet.json` track the last processed candle and history. Before a Testnet order is submitted, the bot saves a pending intent. If the network fails after submission, it queries that client order ID on the next run. If status still cannot be confirmed, automation reports an error and sends no new order. Reconcile the order in Spot Testnet before changing the pending state. The dashboard cannot place or retry orders.
# binance-auto

## Consensus Trade Map v1

The M5 decision engine scores ten observable conditions: SMA trend, RSI, MACD, three-candle momentum, volume, ATR regime, order-book imbalance, funding, open-interest change, and top-trader position ratio. A Long or Short needs at least a 4-point total score and matching technical confirmation; otherwise the engine returns WAIT. Futures Demo executes both directions in one-way mode with isolated 1x leverage, a 60 USDT notional cap, ATR-based stop distance, and a 2R target. Demo stop/target checks are process-driven every minute, so they are not exchange-native protection if the local server is offline. Real trading retains its stricter guarded implementation until the two-way execution path receives separate live validation.
### Backtest validation gate

The dashboard runs a 360-candle walk-forward replay of the six historically reproducible technical factors. Entries occur after a closed signal candle, use the same ATR/0.4% stop distance and 2R target, and subtract 0.08% round-trip fees. Results are marked PROVISIONAL because historical order-book snapshots and the exact live funding/OI/top-trader state are not reconstructed. New Demo entries are blocked until the sample has at least 20 closed trades, profit factor >= 1.20, positive expectancy, and max drawdown <= 10%. Existing positions remain managed even when the gate fails.