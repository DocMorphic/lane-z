import { mkdirSync, createWriteStream } from "node:fs";
import { dirname } from "node:path";

const [path, secondsArg] = process.argv.slice(2);
const duration = Number(secondsArg);
if (!path || !Number.isSafeInteger(duration) || duration < 145 || duration > 3600) {
  throw new Error("Usage: node scripts/capture_native_flow.mjs OUT.jsonl SECONDS (145–3600)");
}
mkdirSync(dirname(path), { recursive: true });
const file = createWriteStream(path, { flags: "wx" });
const assets = new Set(["BTC", "ETH", "SOL"]);
const socket = new WebSocket("wss://api.hyperliquid.xyz/ws");
let finished = false;
let timer;
let heartbeat;
const counts = { bbo: 0, trades: 0 };

function record(row) {
  file.write(JSON.stringify(row) + "\n");
}

function stop(reason) {
  if (finished) return;
  finished = true;
  clearInterval(heartbeat);
  clearTimeout(timer);
  record({ channel: "end", received: Date.now(), reason });
  socket.close();
  file.end();
  console.log(JSON.stringify({ ...counts, reason }));
  if (reason !== "duration") process.exitCode = 1;
}

file.on("error", error => {
  console.error(error.message);
  process.exit(1);
});
socket.addEventListener("open", () => {
  record({ channel: "start", received: Date.now() });
  for (const coin of assets) {
    for (const type of ["bbo", "trades"]) {
      socket.send(JSON.stringify({ method: "subscribe", subscription: { type, coin } }));
    }
  }
  heartbeat = setInterval(() => socket.send('{"method":"ping"}'), 25_000);
  clearTimeout(timer);
  timer = setTimeout(() => stop("duration"), duration * 1000);
});
socket.addEventListener("message", event => {
  if (finished) return;
  const received = Date.now();
  const { channel, data } = JSON.parse(event.data);
  if (channel === "bbo" && assets.has(data.coin)) {
    const { coin, time, bbo: [bid, ask] } = data;
    record({ channel, coin, received, time, bid, ask });
    counts.bbo += 1;
  }
  if (channel === "trades") {
    for (const trade of data) {
      const { coin, time, tid, side, px, sz } = trade;
      if (!assets.has(coin)) continue;
      record({ channel, coin, received, time, tid, side, px, sz });
      counts.trades += 1;
    }
  }
});
socket.addEventListener("error", () => stop("connection error"));
socket.addEventListener("close", () => stop("connection closed"));
process.on("SIGINT", () => stop("interrupted"));
process.on("SIGTERM", () => stop("interrupted"));
timer = setTimeout(() => stop("connection timeout"), 15_000);
