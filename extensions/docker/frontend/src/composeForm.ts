/** Formular-Ansicht einer Compose-Datei: liest Dienste aus dem Text und ändert nur die betroffene Stelle im Text. */
import { isAlias, isMap, isScalar, isSeq, parseDocument, type Document, type Node, type Pair, type Scalar, type YAMLMap, type YAMLSeq } from "yaml";

export type ScalarField = "image" | "restart";
export type ListFieldName = "ports" | "volumes";

export interface ListItem { value: string; editable: boolean }
export interface EnvEntry { key: string; value: string; editable: boolean }
export interface ServiceForm {
  name: string;
  /** Grund, warum der Dienst nur im Text geht. */
  locked: string | null;
  image: string | null;
  imageLocked: string | null;
  restart: string | null;
  restartLocked: string | null;
  ports: { items: ListItem[]; locked: string | null };
  volumes: { items: ListItem[]; locked: string | null };
  environment: { list: boolean; entries: EnvEntry[]; locked: string | null };
}
export interface ComposeForm {
  /** Grund, warum die ganze Datei nur im Text geht. */
  locked: string | null;
  services: ServiceForm[];
  notes: string[];
}

export type FormOp =
  | { op: "set"; service: string; field: ScalarField; value: string }
  | { op: "unset"; service: string; field: ScalarField }
  | { op: "addItem"; service: string; field: ListFieldName; value: string }
  | { op: "setItem"; service: string; field: ListFieldName; index: number; value: string }
  | { op: "removeItem"; service: string; field: ListFieldName; index: number }
  | { op: "setEnv"; service: string; key: string; value: string }
  | { op: "removeEnv"; service: string; key: string }
  | { op: "addService"; name: string; image: string };

export type OpResult = { text: string } | { error: string };

export const SERVICE_NAME_RE = /^[a-zA-Z0-9][a-zA-Z0-9_.-]*$/;
export const ENV_KEY_RE = /^[^=\s]+$/;
const ONLY_TEXT = "Bitte im Text ändern.";
const TAGGED = `Mit einer Markierung wie !reset oder !override geschrieben. ${ONLY_TEXT}`;
const NOT_TEXT = "Diesen Namen liest YAML nicht als Text, sondern etwa als Zahl, Datum oder Wahrheitswert. Nimm einen anderen Namen.";
const NOT_SAFE = `Das lässt sich im Formular nicht sicher ändern, weil die Stelle in der Datei besonders geschrieben ist oder an anderer Stelle mitbenutzt wird. ${ONLY_TEXT}`;
// Wörter, die YAML als Wahrheitswert oder null lesen kann; als Dienst- oder Variablenname nicht erlaubt.
const AMBIGUOUS_WORDS = /^(y|yes|n|no|on|off|true|false|null|~)$/i;
const AMBIGUOUS = [
  // Compose liest Zahlen mit `_`, Datumsangaben und `<<` anders als diese Bibliothek; alles, was so beginnt, kommt in Anführungszeichen.
  /^[0-9+\-.]/,
  /^<</,
  /^(y|yes|n|no|on|off|true|false|null|~)$/i,
  /^[-+]?[0-9][0-9_]*(:[0-5]?[0-9])+(\.[0-9_]*)?$/,
  /^[-+]?0[0-7_]+$/,
  /^[-+]?0b[01_]+$/,
];

interface Splice { from: number; to: number; insert: string }
type Js = Record<string, unknown>;

function parse(text: string): Document.Parsed {
  return parseDocument(text, { prettyErrors: false });
}

function lineStart(text: string, offset: number): number {
  return text.lastIndexOf("\n", offset - 1) + 1;
}

function lineEnd(text: string, offset: number): number {
  if (offset > 0 && text[offset - 1] === "\n") return offset;
  const next = text.indexOf("\n", offset);
  return next < 0 ? text.length : next + 1;
}

function blankBefore(text: string, start: number): boolean {
  return start > 0 && text.slice(lineStart(text, start - 1), start).trim() === "";
}

function column(text: string, offset: number): number {
  return offset - lineStart(text, offset);
}

function str(value: unknown): string {
  return value == null ? "" : String(value);
}

function sameJs(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (typeof a === "number" && typeof b === "number") return Number.isNaN(a) && Number.isNaN(b);
  if (Array.isArray(a)) return Array.isArray(b) && a.length === b.length && a.every((v, i) => sameJs(v, b[i]));
  if (a && b && typeof a === "object" && typeof b === "object" && !Array.isArray(b)) {
    const ka = Object.keys(a);
    return ka.length === Object.keys(b).length && ka.every((k) => k in (b as Js) && sameJs((a as Js)[k], (b as Js)[k]));
  }
  return false;
}

/** Ob YAML und Compose diesen Namen als Text lesen, nicht als Zahl, Datum oder Wahrheitswert. */
function readsAsText(name: string): boolean {
  if (readScalar(name) !== name || AMBIGUOUS_WORDS.test(name)) return false;
  return !/^[-+.]?[0-9]/.test(name) || /[A-Za-z]{2,}/.test(name);
}

/** Der Wert, den YAML aus einem einzelnen Skalar liest. */
function readScalar(source: string): unknown {
  const doc = parse(`k: ${source}\n`);
  if (doc.errors.length) return undefined;
  try {
    return (doc.toJS() as Js | null)?.k;
  } catch {
    return undefined; // etwa `*x`: ein Verweis auf einen Anker, den es nicht gibt
  }
}

/** Schreibt einen Wert als Skalar; Stil und Zahlen-Art wie beim alten Wert, wenn das eindeutig geht. */
function scalarText(value: string, old?: Scalar): { text: string; parsed: unknown } {
  const plainOk = !value.includes("\n") && value.trim() === value && value !== "" && !AMBIGUOUS.some((re) => re.test(value));
  if (plainOk && (!old || old.type === "PLAIN")) {
    const parsed = readScalar(value);
    if (parsed === value) return { text: value, parsed };
  }
  // Eine ganze Zahl bleibt eine Zahl, wenn sie vorher eine war; alles andere Zahlenähnliche liest Compose womöglich anders.
  if (old?.type === "PLAIN" && typeof old.value === "number" && /^-?(0|[1-9][0-9]{0,15})$/.test(value)) return { text: value, parsed: Number(value) };
  if (old?.type === "QUOTE_SINGLE" && !value.includes("\n")) return { text: `'${value.replace(/'/g, "''")}'`, parsed: value };
  return { text: JSON.stringify(value), parsed: value };
}

function quoted(value: string, double: boolean): { text: string; parsed: unknown } {
  return double ? { text: JSON.stringify(value), parsed: value } : scalarText(value);
}

function applySplices(text: string, splices: Splice[]): string {
  let out = text;
  for (const s of [...splices].sort((a, b) => b.from - a.from)) out = out.slice(0, s.from) + s.insert + out.slice(s.to);
  return out;
}

function plainScalar(node: unknown): node is Scalar {
  return isScalar(node) && !node.anchor && !node.tag && (node.type === "PLAIN" || node.type === "QUOTE_DOUBLE" || node.type === "QUOTE_SINGLE");
}

/** Der Wert, wie er in der Datei steht (`1.10` bleibt `1.10`, nicht `1.1`). */
function shown(node: Scalar): string {
  return node.type === "PLAIN" && typeof node.source === "string" ? node.source : str(node.value);
}

function blockMap(node: unknown): node is YAMLMap {
  return isMap(node) && !node.flow && !node.anchor && !node.tag;
}

function findPair(map: YAMLMap, key: string): Pair<Node, Node | null> | undefined {
  return (map.items as Pair<Node, Node | null>[]).find((p) => isScalar(p.key) && String(p.key.value) === key);
}

function scalarLock(node: unknown): string | null {
  if (node == null) return null;
  if (isAlias(node)) return `Der Wert kommt von einer anderen Stelle der Datei (*${node.source}). ${ONLY_TEXT}`;
  if (isScalar(node) && (node.type === "BLOCK_LITERAL" || node.type === "BLOCK_FOLDED")) return `Mehrzeilig geschrieben. ${ONLY_TEXT}`;
  if (isScalar(node) && node.tag) return TAGGED;
  if (!plainScalar(node)) return `Besonders geschrieben. ${ONLY_TEXT}`;
  return null;
}

function listLock(node: unknown): string | null {
  if (node != null && (node as Node).tag) return TAGGED;
  if (node == null || (isScalar(node) && node.value == null && !node.anchor)) return null;
  if (isAlias(node)) return `Die Liste kommt von einer anderen Stelle der Datei (*${node.source}). ${ONLY_TEXT}`;
  if (!isSeq(node) || node.anchor) return `Besonders geschrieben. ${ONLY_TEXT}`;
  return null;
}

function serviceLock(node: unknown): string | null {
  if (isAlias(node)) return `Der Dienst kommt von einer anderen Stelle der Datei. ${ONLY_TEXT}`;
  if (!isMap(node)) return `Der Dienst ist leer oder besonders geschrieben. ${ONLY_TEXT}`;
  if (node.flow) return `Der Dienst ist in einer Zeile geschrieben. ${ONLY_TEXT}`;
  if (node.tag) return TAGGED;
  if (node.anchor) return `Der Dienst wird an anderer Stelle der Datei mitbenutzt (&${node.anchor}). ${ONLY_TEXT}`;
  if (findPair(node, "<<")) return `Der Dienst übernimmt Einstellungen von einer anderen Stelle der Datei (<<). ${ONLY_TEXT}`;
  if (findPair(node, "extends")) return `Der Dienst übernimmt Einstellungen eines anderen Dienstes (extends). ${ONLY_TEXT}`;
  return null;
}

function itemSummary(text: string, node: Node): string {
  const [from, to] = node.range ?? [0, 0];
  return text.slice(from, to).replace(/\s+/g, " ").trim();
}

function readList(text: string, node: unknown): { items: ListItem[]; locked: string | null } {
  const locked = listLock(node);
  if (locked || !isSeq(node)) return { items: [], locked };
  const items = (node.items as Node[]).map((item) =>
    plainScalar(item) ? { value: shown(item), editable: true } : { value: itemSummary(text, item), editable: false });
  return { items, locked: null };
}

function envEntry(raw: string): { key: string; value: string } {
  const at = raw.indexOf("=");
  return at < 0 ? { key: raw, value: "" } : { key: raw.slice(0, at), value: raw.slice(at + 1) };
}

function readEnv(text: string, node: unknown): ServiceForm["environment"] {
  if (node != null && (node as Node).tag) return { list: false, entries: [], locked: TAGGED };
  if (node == null || (isScalar(node) && node.value == null && !node.anchor)) return { list: false, entries: [], locked: null };
  if (isSeq(node) && !node.anchor) {
    const entries = (node.items as Node[]).map((item) =>
      plainScalar(item) ? { ...envEntry(shown(item)), editable: true } : { key: itemSummary(text, item), value: "", editable: false });
    return { list: true, entries, locked: null };
  }
  if (isMap(node) && !node.anchor) {
    const entries = (node.items as Pair<Node, Node | null>[]).map((pair) => {
      const key = isScalar(pair.key) ? str(pair.key.value) : itemSummary(text, pair.key);
      const value = pair.value;
      const editable = isScalar(pair.key) && (value == null || (plainScalar(value) && (value.value != null || value.source === "")));
      return { key, value: isScalar(value) ? shown(value) : value ? itemSummary(text, value) : "", editable };
    });
    return { list: false, entries, locked: null };
  }
  return { list: false, entries: [], locked: `Besonders geschrieben. ${ONLY_TEXT}` };
}

function fileLock(doc: Document.Parsed, text: string): string | null {
  if (doc.errors.length) {
    const first = doc.errors[0];
    if (first.code === "MULTIPLE_DOCS") return `Die Datei enthält mehrere YAML-Dokumente. ${ONLY_TEXT}`;
    const line = text.slice(0, first.pos[0]).split("\n").length;
    return `Die Datei ist so kein gültiges YAML (Zeile ${line}). ${ONLY_TEXT}`;
  }
  try {
    doc.toJS();
  } catch {
    return `Die Datei verweist zu oft auf andere Stellen. ${ONLY_TEXT}`;
  }
  if (doc.contents != null && !blockMap(doc.contents)) return `Die Datei ist nicht wie eine Compose-Datei aufgebaut. ${ONLY_TEXT}`;
  const services = doc.contents ? findPair(doc.contents as YAMLMap, "services")?.value : null;
  if (services != null && !(isScalar(services) && services.value == null && services.source === "") && !blockMap(services)) {
    return `Der Abschnitt „services“ ist besonders geschrieben. ${ONLY_TEXT}`;
  }
  return null;
}

/** Liest Dienste, Images, Ports, Ordner, Umgebung und Neustart-Regel aus dem Text. */
export function readForm(text: string): ComposeForm {
  const doc = parse(text);
  const locked = fileLock(doc, text);
  if (locked) return { locked, services: [], notes: [] };
  const root = doc.contents as YAMLMap | null;
  const notes: string[] = [];
  if (root && findPair(root, "include")) notes.push("Weitere Dienste kommen aus eingebundenen Dateien (include); die zeigt das Formular nicht.");
  const services = root ? findPair(root, "services")?.value : null;
  if (!blockMap(services)) return { locked: null, services: [], notes };
  const out: ServiceForm[] = [];
  for (const pair of services.items as Pair<Node, Node | null>[]) {
    const name = isScalar(pair.key) ? str(pair.key.value) : itemSummary(text, pair.key);
    const lock = isScalar(pair.key) ? serviceLock(pair.value) : `Besonders geschrieben. ${ONLY_TEXT}`;
    const map = !lock ? (pair.value as YAMLMap) : null;
    const field = (key: string) => (map ? findPair(map, key)?.value ?? null : null);
    const image = field("image");
    const restart = field("restart");
    out.push({
      name,
      locked: lock,
      image: isScalar(image) && image.value != null ? shown(image) : null,
      imageLocked: scalarLock(image),
      restart: isScalar(restart) && restart.value != null ? shown(restart) : null,
      restartLocked: scalarLock(restart),
      ports: map ? readList(text, field("ports")) : { items: [], locked: lock },
      volumes: map ? readList(text, field("volumes")) : { items: [], locked: lock },
      environment: map ? readEnv(text, field("environment")) : { list: false, entries: [], locked: lock },
    });
  }
  return { locked: null, services: out, notes };
}

/** Einrückung je Stufe und wie weit Listen unter ihrem Schlüssel eingerückt sind, abgelesen an der Datei. */
function style(text: string, doc: Document.Parsed): { unit: number; seq: number } {
  let unit = 2;
  const root = doc.contents as YAMLMap | null;
  const services = root ? findPair(root, "services") : undefined;
  if (services && blockMap(services.value) && services.value.items.length) {
    const first = (services.value.items[0] as Pair<Node, Node>).key;
    const diff = column(text, first.range![0]) - column(text, services.key.range![0]);
    if (diff > 0) unit = diff;
  }
  let seq: number | null = null;
  const walk = (node: unknown): void => {
    if (seq != null || !isMap(node) || node.flow) return;
    for (const pair of node.items as Pair<Node, Node | null>[]) {
      if (seq != null) return;
      if (isSeq(pair.value) && !pair.value.flow && pair.value.range && pair.key.range) {
        seq = Math.max(0, column(text, pair.value.range[0]) - column(text, pair.key.range[0]));
      } else walk(pair.value);
    }
  };
  walk(root);
  return { unit, seq: seq ?? unit };
}

function pairEnd(text: string, pair: Pair<Node, Node | null>): number {
  const ends = [pair.key.range![2]];
  if (pair.value?.range) ends.push(pair.value.range[2]);
  return lineEnd(text, Math.max(...ends));
}

function removePair(text: string, pair: Pair<Node, Node | null>): Splice {
  return { from: lineStart(text, pair.key.range![0]), to: pairEnd(text, pair), insert: "" };
}

/** Neue Zeilen am Ende einer Map (Block-Schreibweise), eingerückt wie ihre Schlüssel. */
function appendToMap(text: string, map: YAMLMap, lines: (indent: string) => string): Splice {
  const pairs = map.items as Pair<Node, Node | null>[];
  const last = pairs[pairs.length - 1];
  const at = pairEnd(text, last);
  const indent = " ".repeat(column(text, pairs[0].key.range![0]));
  const before = at === text.length && !text.endsWith("\n") ? "\n" : "";
  return { from: at, to: at, insert: before + lines(indent) };
}

function setScalarSplice(node: Scalar, insert: string): Splice {
  const [from, to] = node.range!;
  if (node.value == null && node.source === "") return { from, to, insert: ` ${insert}` };
  return { from, to, insert };
}

function seqLines(indent: string, seqIndent: number, items: string[]): string {
  return items.map((item) => `${indent}${" ".repeat(seqIndent)}- ${item}\n`).join("");
}

function verify(before: string, after: string, expect: (js: Js) => void): OpResult {
  const doc = parse(after);
  if (doc.errors.length) return { error: NOT_SAFE };
  let wanted: Js;
  let got: unknown;
  try {
    wanted = (parse(before).toJS() as Js | null) ?? {};
    expect(wanted);
    got = doc.toJS() ?? {};
  } catch {
    return { error: NOT_SAFE };
  }
  return sameJs(got, wanted) ? { text: after } : { error: NOT_SAFE };
}

function serviceJs(js: Js, name: string): Js {
  return (js.services as Js)[name] as Js;
}

function itemsDouble(seq: YAMLSeq, field: ListFieldName): boolean {
  const scalars = (seq.items as Node[]).filter(plainScalar);
  if (!scalars.length) return field === "ports";
  return scalars.every((s) => s.type === "QUOTE_DOUBLE");
}

/** Führt eine Änderung aus dem Formular am Text aus; der Rest der Datei bleibt Zeichen für Zeichen gleich. */
export function applyOp(text: string, op: FormOp): OpResult {
  const doc = parse(text);
  if (fileLock(doc, text)) return { error: NOT_SAFE };
  const { unit, seq } = style(text, doc);
  const root = doc.contents as YAMLMap | null;

  if (op.op === "addService") {
    if (!SERVICE_NAME_RE.test(op.name)) return { error: "Der Name darf nur Buchstaben, Ziffern, „-“, „_“ und „.“ enthalten und muss mit einem Buchstaben oder einer Ziffer anfangen." };
    if (!readsAsText(op.name)) return { error: NOT_TEXT };
    const image = scalarText(op.image);
    const block = (indent: string) => `${indent}${op.name}:\n${indent}${" ".repeat(unit)}image: ${image.text}\n`;
    const servicesPair = root ? findPair(root, "services") : undefined;
    let splice: Splice;
    if (servicesPair && blockMap(servicesPair.value)) {
      if (findPair(servicesPair.value, op.name)) return { error: `Einen Dienst „${op.name}“ gibt es schon.` };
      const second = (servicesPair.value.items[1] as Pair<Node, Node> | undefined)?.key.range?.[0];
      const spaced = second != null && blankBefore(text, lineStart(text, second));
      const s = appendToMap(text, servicesPair.value, block);
      splice = spaced ? { ...s, insert: s.insert.replace(/^(\n?)/, "$1\n") } : s;
    } else if (servicesPair) {
      const at = lineEnd(text, servicesPair.key.range![2]);
      const indent = " ".repeat(column(text, servicesPair.key.range![0]) + unit);
      splice = { from: at, to: at, insert: (at === text.length && !text.endsWith("\n") ? "\n" : "") + block(indent) };
    } else {
      const at = text.length;
      const lead = text === "" || text.endsWith("\n") ? "" : "\n";
      splice = { from: at, to: at, insert: `${lead}services:\n${block(" ".repeat(unit))}` };
    }
    return verify(text, applySplices(text, [splice]), (js) => {
      js.services = (js.services as Js | null) ?? {};
      (js.services as Js)[op.name] = { image: image.parsed };
    });
  }

  const services = root ? findPair(root, "services")?.value : null;
  if (!blockMap(services)) return { error: NOT_SAFE };
  const servicePair = findPair(services, op.service);
  if (!servicePair || serviceLock(servicePair.value)) return { error: NOT_SAFE };
  const map = servicePair.value as YAMLMap;

  if (op.op === "set" || op.op === "unset") {
    const pair = findPair(map, op.field);
    if (op.op === "unset") {
      if (!pair) return { text };
      if (scalarLock(pair.value)) return { error: NOT_SAFE };
      return verify(text, applySplices(text, [removePair(text, pair)]), (js) => { delete serviceJs(js, op.service)[op.field]; });
    }
    if (pair && scalarLock(pair.value)) return { error: NOT_SAFE };
    const old = isScalar(pair?.value) ? pair.value : undefined;
    const value = scalarText(op.value, old?.value != null ? old : undefined);
    const splice = pair && old
      ? setScalarSplice(old, value.text)
      : pair
        ? { from: lineStart(text, pair.key.range![0]), to: pairEnd(text, pair), insert: `${" ".repeat(column(text, pair.key.range![0]))}${op.field}: ${value.text}\n` }
        : appendToMap(text, map, (indent) => `${indent}${op.field}: ${value.text}\n`);
    return verify(text, applySplices(text, [splice]), (js) => { serviceJs(js, op.service)[op.field] = value.parsed; });
  }

  if (op.op === "addItem" || op.op === "setItem" || op.op === "removeItem") {
    const pair = findPair(map, op.field);
    const node = pair?.value;
    if (listLock(node)) return { error: NOT_SAFE };
    const list = isSeq(node) ? node : null;
    if (op.op === "addItem") {
      const value = quoted(op.value, list ? itemsDouble(list, op.field) : op.field === "ports");
      let splice: Splice;
      if (list && list.items.length && list.flow) {
        const last = list.items[list.items.length - 1] as Node;
        splice = { from: last.range![1], to: last.range![1], insert: `, ${value.text}` };
      } else if (list && list.items.length) {
        const first = list.items[0] as Node;
        const last = list.items[list.items.length - 1] as Node;
        const at = lineEnd(text, last.range![2]);
        const dash = column(text, list.range![0]);
        if (column(text, first.range![0]) <= dash) return { error: NOT_SAFE };
        splice = { from: at, to: at, insert: (at === text.length && !text.endsWith("\n") ? "\n" : "") + `${" ".repeat(dash)}- ${value.text}\n` };
      } else if (pair) {
        const indent = " ".repeat(column(text, pair.key.range![0]));
        splice = { from: lineStart(text, pair.key.range![0]), to: pairEnd(text, pair), insert: `${indent}${op.field}:\n${seqLines(indent, seq, [value.text])}` };
      } else {
        splice = appendToMap(text, map, (indent) => `${indent}${op.field}:\n${seqLines(indent, seq, [value.text])}`);
      }
      return verify(text, applySplices(text, [splice]), (js) => {
        const svc = serviceJs(js, op.service);
        svc[op.field] = [...((svc[op.field] as unknown[] | null) ?? []), value.parsed];
      });
    }
    const item = list?.items[op.index] as Node | undefined;
    if (!list || !item) return { error: NOT_SAFE };
    if (op.op === "setItem") {
      if (!plainScalar(item)) return { error: NOT_SAFE };
      const value = scalarText(op.value, item);
      return verify(text, applySplices(text, [setScalarSplice(item, value.text)]), (js) => {
        (serviceJs(js, op.service)[op.field] as unknown[])[op.index] = value.parsed;
      });
    }
    let splice: Splice;
    if (list.items.length === 1) splice = removePair(text, pair!);
    else if (list.flow) {
      const next = list.items[op.index + 1] as Node | undefined;
      const prev = list.items[op.index - 1] as Node | undefined;
      splice = next ? { from: item.range![0], to: next.range![0], insert: "" } : { from: prev!.range![1], to: item.range![1], insert: "" };
    } else {
      splice = { from: lineStart(text, item.range![0]), to: lineEnd(text, item.range![2]), insert: "" };
    }
    return verify(text, applySplices(text, [splice]), (js) => {
      const svc = serviceJs(js, op.service);
      const rest = (svc[op.field] as unknown[]).filter((_, i) => i !== op.index);
      if (rest.length) svc[op.field] = rest;
      else delete svc[op.field];
    });
  }

  // Umgebung: als Map („KEY: wert“) oder als Liste („KEY=wert“), so wie sie in der Datei steht.
  if (!ENV_KEY_RE.test(op.key)) return { error: "Der Name einer Variablen darf weder „=“ noch Leerzeichen enthalten." };
  const pair = findPair(map, "environment");
  const node = pair?.value;
  if (isSeq(node) && !node.anchor && !node.tag) {
    const index = (node.items as Node[]).findIndex((item) => plainScalar(item) && envEntry(str(item.value)).key === op.key);
    if (op.op === "removeEnv") return index < 0 ? { text } : applyOp(text, { op: "removeItem", service: op.service, field: "environment" as ListFieldName, index });
    const raw = `${op.key}=${op.value}`;
    if (index >= 0) return applyOp(text, { op: "setItem", service: op.service, field: "environment" as ListFieldName, index, value: raw });
    return applyOp(text, { op: "addItem", service: op.service, field: "environment" as ListFieldName, value: raw });
  }
  if (node != null && !(isScalar(node) && node.value == null && node.source === "") && (!isMap(node) || node.anchor || node.tag)) return { error: NOT_SAFE };
  const env = isMap(node) ? node : null;
  const entry = env ? findPair(env, op.key) : undefined;
  if (op.op === "removeEnv") {
    if (!entry || !env) return { text };
    if (env.flow || (entry.value != null && !plainScalar(entry.value))) return { error: NOT_SAFE };
    const splice = env.items.length === 1 ? removePair(text, pair!) : removePair(text, entry);
    return verify(text, applySplices(text, [splice]), (js) => {
      const svc = serviceJs(js, op.service);
      const rest = { ...(svc.environment as Js) };
      delete rest[op.key];
      if (Object.keys(rest).length) svc.environment = rest;
      else delete svc.environment;
    });
  }
  const key = scalarText(op.key);
  if (key.parsed !== op.key || !readsAsText(op.key)) return { error: NOT_TEXT };
  let splice: Splice;
  let parsed: unknown;
  if (entry) {
    const old = entry.value;
    if (old != null && !plainScalar(old)) return { error: NOT_SAFE };
    const value = scalarText(op.value, old && old.value != null ? old : undefined);
    parsed = value.parsed;
    splice = old ? setScalarSplice(old, value.text) : { from: entry.key.range![1], to: entry.key.range![1], insert: `: ${value.text}` };
    if (!old && text[entry.key.range![1]] === ":") splice = { from: entry.key.range![1] + 1, to: entry.key.range![1] + 1, insert: ` ${value.text}` };
  } else {
    const value = scalarText(op.value);
    parsed = value.parsed;
    if (env && env.flow) {
      if (!env.items.length) return { error: NOT_SAFE };
      const last = env.items[env.items.length - 1] as Pair<Node, Node | null>;
      const end = last.value?.range?.[1] ?? last.key.range![1];
      splice = { from: end, to: end, insert: `, ${key.text}: ${value.text}` };
    } else if (env && env.items.length) {
      splice = appendToMap(text, env, (indent) => `${indent}${key.text}: ${value.text}\n`);
    } else if (pair) {
      const indent = " ".repeat(column(text, pair.key.range![0]));
      splice = { from: lineStart(text, pair.key.range![0]), to: pairEnd(text, pair), insert: `${indent}environment:\n${indent}${" ".repeat(unit)}${key.text}: ${value.text}\n` };
    } else {
      splice = appendToMap(text, map, (indent) => `${indent}environment:\n${indent}${" ".repeat(unit)}${key.text}: ${value.text}\n`);
    }
  }
  return verify(text, applySplices(text, [splice]), (js) => {
    const svc = serviceJs(js, op.service);
    svc.environment = { ...((svc.environment as Js | null) ?? {}), [op.key]: parsed };
  });
}
