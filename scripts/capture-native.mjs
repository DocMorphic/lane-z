import { createWriteStream, mkdirSync } from "node:fs";
import { dirname } from "node:path";
import { performance } from "node:perf_hooks";
import { pipeline } from "node:stream/promises";
import { createGzip } from "node:zlib";

const [path, argument, mode, ...extra] = process.argv.slice(2);
const seconds = Number(argument);
if (!path || !Number.isInteger(seconds) || seconds < 1 || seconds > 3600 ||
    (mode !== undefined && mode !== "--bbo") || extra.length) {
  throw new Error("Usage: node scripts/capture-native.mjs OUTPUT.jsonl.gz SECONDS [--bbo]");
}

const coins = ["BTC", "ETH", "SOL", "HYPE", "DOGE", "SUI"];
mkdirSync(dirname(path), { recursive: true });
const output = createGzip();
const flushed = pipeline(output, createWriteStream(path, { flags: "wx" }));
void flushed.catch(error => {
  console.error(error.message);
  process.exitCode = 1;
  void stop();
});
const socket = new WebSocket("wss://api.hyperliquid.xyz/ws");
const bookChannel = mode === "--bbo" ? "bbo" : "l2Book";
const counts = { [bookChannel]: 0, trades: 0 };
let stopped = false;

function receivedMs() {
  return Math.floor(performance.timeOrigin + performance.now());
}

function write(channel, data, received_ms = receivedMs()) {
  output.write(JSON.stringify({ received_ms, channel, data }) + "\n");
}

socket.addEventListener("open", () => {
  write("connected", coins);
  for (const coin of coins) {
    for (const type of Object.keys(counts)) {
      socket.send(JSON.stringify({ method: "subscribe", subscription: { type, coin } }));
    }
  }
});

socket.addEventListener("message", event => {
  if (stopped) return;
  const received_ms = receivedMs();
  const { channel, data } = JSON.parse(event.data);
  if (!Object.hasOwn(counts, channel)) return;
  counts[channel]++;
  const fields = channel === "trades" ? data.map(({ coin, time, tid, side, px, sz }) => ({ coin, time, tid, side, px, sz })) : data;
  write(channel, fields, received_ms);
});

socket.addEventListener("close", () => void stop(true));
socket.addEventListener("error", () => void stop(true));
const heartbeat = setInterval(() => {
  if (socket.readyState === WebSocket.OPEN) socket.send('{"method":"ping"}');
}, 25000);
const timeout = setTimeout(() => void stop(), seconds * 1000);

async function stop(incomplete = false) {
  if (stopped) return;
  stopped = true;
  clearInterval(heartbeat);
  clearTimeout(timeout);
  if (socket.readyState === WebSocket.OPEN) socket.close();
  if (!output.destroyed) {
    write("ended", { ...counts, incomplete });
    output.end();
    await flushed;
  }
  if (incomplete || !counts[bookChannel] || !counts.trades) process.exitCode = 1;
  console.log(JSON.stringify(counts));
}

process.on("SIGINT", () => void stop(true));
process.on("SIGTERM", () => void stop(true));
