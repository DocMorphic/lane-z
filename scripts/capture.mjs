import { createWriteStream, mkdirSync } from "node:fs";
import { join } from "node:path";
import { pipeline } from "node:stream/promises";
import { createGzip } from "node:zlib";

const [directory, secondsArg] = process.argv.slice(2);
const seconds = Number(secondsArg);
if (!directory || !Number.isSafeInteger(seconds) || seconds < 1 || seconds > 86_400) {
  throw new Error("Usage: node scripts/capture.mjs OUTPUT_DIRECTORY SECONDS (1–86400)");
}

const hyperliquid = ["NVDA", "TSLA", "XYZ100", "AAPL", "MSFT"];
const binance = ["NVDAUSDT", "TSLAUSDT"];
const streams = binance.map(symbol => symbol.toLowerCase() + "@bookTicker").join("/");
const files = new Map();
const sockets = new Set();
const timeout = setTimeout(() => stop(), seconds * 1000);
let stopped = false;
let heartbeat;

mkdirSync(directory, { recursive: true });

function record(exchange, symbol, venueMs, receivedMs, prices) {
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
    gzip.write("exchange,symbol,timestamp,local_timestamp,ask_amount,ask_price,bid_price,bid_amount\n");
    files.set(path, { gzip, flushed, rows: 0, symbol });
  }
  const file = files.get(path);
  const row = [exchange, symbol, venueMs * 1000, receivedMs * 1000, ...prices];
  file.gzip.write(row.join(",") + "\n");
  file.rows++;
}

function connect(url, onOpen, onQuote) {
  const socket = new WebSocket(url);
  sockets.add(socket);
  socket.addEventListener("open", () => onOpen(socket));
  socket.addEventListener("message", event => {
    if (stopped) return;
    const receivedMs = Date.now();
    try {
      onQuote(JSON.parse(event.data), receivedMs);
    } catch (error) {
      void stop(error.message);
    }
  });
  socket.addEventListener("error", event => {
    void stop(new URL(url).host + ": " + (event.message || "connection failed"));
  });
  socket.addEventListener("close", event => {
    void stop(new URL(url).host + " closed the feed (" + event.code + ")");
  });
  return socket;
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

const hyperSocket = connect("wss://api.hyperliquid.xyz/ws", socket => {
  for (const symbol of hyperliquid) {
    socket.send(JSON.stringify({ method: "subscribe", subscription: { type: "bbo", coin: "xyz:" + symbol } }));
  }
}, (message, receivedMs) => {
  if (message.channel !== "bbo") return;
  const { coin, time, bbo: [bid, ask] } = message.data;
  const symbol = coin.slice(4);
  if (hyperliquid.includes(symbol) && Number.isSafeInteger(time)) {
    record("hyperliquid", "XYZ:" + symbol, time, receivedMs, [ask?.sz, ask?.px, bid?.px, bid?.sz]);
  }
});
heartbeat = setInterval(() => {
  if (hyperSocket.readyState === WebSocket.OPEN) hyperSocket.send('{"method":"ping"}');
}, 25_000);

connect("wss://fstream.binance.com/public/stream?streams=" + streams, () => {}, (message, receivedMs) => {
  const quote = message.data;
  if (quote?.e === "bookTicker" && binance.includes(quote.s) && Number.isSafeInteger(quote.T)) {
    record("binance-futures", quote.s, quote.T, receivedMs, [quote.A, quote.a, quote.b, quote.B]);
  }
});

process.on("SIGINT", () => void stop());
