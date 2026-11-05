import { Terminal } from "@xterm/xterm";
import "@xterm/xterm/css/xterm.css";
import { installStyleNonce } from "../../extensions/_shared/frontend/src/csp";
installStyleNonce();

const statusEl = document.getElementById("status");
const term = new Terminal({ cols: 80, rows: 24 });
term.open(document.getElementById("term"));
term.writeln("(noch nicht verbunden)");

function log(msg) {
  statusEl.textContent += "\n" + msg;
}

document.getElementById("connect").addEventListener("click", async () => {
  const username = document.getElementById("username").value;
  const password = document.getElementById("password").value;
  const hostId = document.getElementById("hostId").value;
  statusEl.textContent = "Login...";

  const loginResp = await fetch("/api/v1/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  const loginData = await loginResp.json();
  if (!loginResp.ok) {
    log("Login fehlgeschlagen: " + JSON.stringify(loginData));
    return;
  }
  const token = loginData.access_token;
  log("Login ok.");

  const sessResp = await fetch("/api/v1/terminal/sessions", {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: "Bearer " + token },
    body: JSON.stringify({ host_id: hostId, cols: 80, rows: 24 }),
  });
  const sessData = await sessResp.json();
  if (!sessResp.ok) {
    log("Session-Erstellung fehlgeschlagen: " + JSON.stringify(sessData));
    return;
  }
  log("Session erstellt: " + sessData.session_id);

  const wsProto = location.protocol === "https:" ? "wss:" : "ws:";
  const wsUrl = wsProto + "//" + location.host + sessData.ws_url;
  const ws = new WebSocket(wsUrl);
  ws.binaryType = "arraybuffer";

  ws.onopen = () => log("WS verbunden.");
  ws.onclose = (ev) => log("WS geschlossen: code=" + ev.code);
  ws.onerror = () => log("WS-Fehler.");
  ws.onmessage = (ev) => {
    if (typeof ev.data === "string") {
      log("Kontrollnachricht: " + ev.data);
    } else {
      const bytes = new Uint8Array(ev.data);
      term.write(bytes);
    }
  };

  term.onData((data) => {
    const bytes = new TextEncoder().encode(data);
    ws.send(bytes);
  });
});
  