import { createWriteStream, mkdirSync } from "node:fs";
import { join } from "node:path";
import { pipeline } from "node:stream/promises";
import { createGzip } from "node:zlib";

const [directory, secondsArg, mode, ...extra] = process.argv.slice(2);
const seconds = Number(secondsArg);
const modes = ["--hyperliquid-trades-only", "--hyperliquid-context-only"];
if (!directory || !Number.isSafeInteger(seconds) || seconds < 1 || seconds > 86_400 ||
    (mode !== undefined && !modes.includes(mode)) || extra.length) {
  throw new Error("Usage: node scripts/capture.mjs OUTPUT_DIRECTORY SECONDS [--hyperliquid-trades-only|--hyperliquid-context-only]");
}

const tradesOnly = mode === "--hyperliquid-trades-only";
const contextOnly = mode === "--hyperliquid-context-only";
const hyperliquid = mode ? ["NVDA", "TSLA"] : ["NVDA", "TSLA", "XYZ100", "AAPL", "MSFT"];
const binance = mode ? [] : ["NVDAUSDT", "TSLAUSDT"];
const hyperChannel = tradesOnly ? "trades" : contextOnly ? "activeAssetCtx" : "bbo";
const columns = {
  trades: "trade_id,side,price,amount",
  activeAssetCtx: "index_price,mark_price,funding_rate,open_interest",
  bbo: "ask_amount,ask_price,bid_price,bid_amount",
}[hyperChannel];
const streams = binance.map(symbol => symbol.toLowerCase() + "@bookTicker").join("/");
const files = new Map();
const sockets = new Set();
const timeout = setTimeout(() => stop(), seconds * 1000);
let stopped = false;
let heartbeat;

mkdirSync(directory, { recursive: true });

function record(exchange, symbol, venueMs, receivedMs, fields) {
  const day = new Date(receivedMs).toISOString().slice(0, 10);
  const fileSymbol = symbol.replace("XYZ:", "XYZ-");
  const path = join(directory, day + "-" + fileSymbol + ".csv.gz");
  if (!files.has(path)) {
    const gzip = createGzip();
    const flushed = pipeline(gzip, createWriteStream(path, { flags: "wx" })).catch(error => {
      console.error(path + ": " + error.message);
      process.exitCode = 1;
      void stop();
    });
    gzip.write("exchange,symbol,timestamp,local_timestamp," + columns + "\n");
    files.set(path, { gzip, flushed, rows: 0, symbol });
  }
  const file = files.get(path);
  const row = [exchange, symbol, venueMs * 1000, receivedMs * 1000, ...fields];
  file.gzip.write(row.join(",") + "\n");
  file.rows++;
}

function connect(url, onOpen, onQuote) {
  const host = new URL(url).host;
  let activeSocket;
  let retryMs = 1_000;

  function dial() {
    if (stopped) return;
    const socket = new WebSocket(url);
    activeSocket = socket;
    sockets.add(socket);
    socket.addEventListener("open", () => {
      retryMs = 1_000;
      onOpen(socket);
    });
    socket.addEventListener("message", event => {
      if (stopped) return;
      const receivedMs = Date.now();
      try {
        onQuote(JSON.parse(event.data), receivedMs);
      } catch (error) {
        void stop(error.message);
      }
    });
    socket.addEventListener("error", () => socket.close());
    socket.addEventListener("close", event => {
      sockets.delete(socket);
      if (stopped) return;
      console.error(`${new Date().toISOString()} ${host} closed (${event.code}). Reconnecting in ${retryMs} ms.`);
      setTimeout(dial, retryMs).unref();
      retryMs = Math.min(retryMs * 2, 30_000);
    });
  }

  dial();
  return () => activeSocket;
}

async function stop(error) {
  if (stopped) return;
  stopped = true;
  clearTimeout(timeout);
  clearInterval(heartbeat);
  for (const socket of sockets) socket.close();
  for (const file of files.values()) file.gzip.end();
  await Promise.all([...files.values()].map(file => file.flushed));
  const counts = Object.fromEntries([...files].map(([path, file]) => [path, file.rows]));
  console.log(JSON.stringify(counts, null, 2));
  const captured = new Set([...files.values()].map(file => file.symbol));
  if (error || captured.size < hyperliquid.length + binance.length) {
    console.error(error || "At least one market did not produce any quote updates.");
    process.exitCode = 1;
  }
}

const currentHyperSocket = connect("wss://api.hyperliquid.xyz/ws", socket => {
  for (const symbol of hyperliquid) {
    socket.send(JSON.stringify({ method: "subscribe", subscription: { type: hyperChannel, coin: "xyz:" + symbol } }));
  }
}, (message, receivedMs) => {
  if (message.channel !== hyperChannel) return;
  if (tradesOnly) {
    for (const trade of message.data) {
      if (hyperliquid.includes(trade.coin.slice(4)) && Number.isSafeInteger(trade.time)) {
        record("hyperliquid", trade.coin.toUpperCase(), trade.time, receivedMs, [trade.tid, trade.side, trade.px, trade.sz]);
      }
    }
    return;
  }
  if (contextOnly) {
    const { coin, ctx } = message.data;
    if (hyperliquid.includes(coin.slice(4))) {
      // The context message has no venue timestamp. Its observation time is the local receipt clock.
      record("hyperliquid", coin.toUpperCase(), receivedMs, receivedMs,
             [ctx.oraclePx, ctx.markPx, ctx.funding, ctx.openInterest]);
    }
    return;
  }
  const { coin, time, bbo: [bid, ask] } = message.data;
  const symbol = coin.slice(4);
  if (hyperliquid.includes(symbol) && Number.isSafeInteger(time)) {
    record("hyperliquid", "XYZ:" + symbol, time, receivedMs, [ask?.sz, ask?.px, bid?.px, bid?.sz]);
  }
});
heartbeat = setInterval(() => {
  const hyperSocket = currentHyperSocket();
  if (hyperSocket.readyState === WebSocket.OPEN) hyperSocket.send('{"method":"ping"}');
}, 25_000);

if (binance.length) {
  connect("wss://fstream.binance.com/public/stream?streams=" + streams, () => {}, (message, receivedMs) => {
    const quote = message.data;
    if (quote?.e === "bookTicker" && binance.includes(quote.s) && Number.isSafeInteger(quote.T)) {
      record("binance-futures", quote.s, quote.T, receivedMs, [quote.A, quote.a, quote.b, quote.B]);
    }
  });
}

process.on("SIGINT", () => void stop());
process.on("SIGTERM", () => void stop());
