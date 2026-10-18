import { describe, expect, it } from "vitest";

import { applyOp, readForm, type FormOp } from "./composeForm";

const FILE = `# Mein Stack
services:
  web:
    image: nginx:1.27   # feste Version
    ports:
      - "8080:80"   # Verwaltung
      # zwischen
      - "443:443"
    environment:
      TZ: Europe/Berlin
      PORT: 8080
    restart: unless-stopped

  db:
    image: 'postgres:16'
    volumes: [ "db:/var/lib/postgresql/data" ]
volumes:
  db: {}
`;

function apply(text: string, op: FormOp): string {
  const out = applyOp(text, op);
  if ("error" in out) throw new Error(out.error);
  return out.text;
}

function failure(text: string, op: FormOp): string {
  const out = applyOp(text, op);
  if (!("error" in out)) throw new Error(`erwartet Fehler, bekam:\n${out.text}`);
  return out.error;
}

describe("readForm", () => {
  it("liest Dienste und Felder", () => {
    const form = readForm(FILE);
    expect(form.locked).toBeNull();
    expect(form.services.map((s) => s.name)).toEqual(["web", "db"]);
    const [web, db] = form.services;
    expect(web.image).toBe("nginx:1.27");
    expect(web.restart).toBe("unless-stopped");
    expect(web.ports.items).toEqual([{ value: "8080:80", editable: true }, { value: "443:443", editable: true }]);
    expect(web.environment).toEqual({
      list: false, locked: null,
      entries: [{ key: "TZ", value: "Europe/Berlin", editable: true }, { key: "PORT", value: "8080", editable: true }],
    });
    expect(db.volumes.items).toEqual([{ value: "db:/var/lib/postgresql/data", editable: true }]);
    expect(db.restart).toBeNull();
  });

  it("leere Datei: keine Dienste, aber bearbeitbar", () => {
    expect(readForm("")).toEqual({ locked: null, services: [], notes: [] });
    expect(readForm("services:\n").locked).toBeNull();
  });

  it("sperrt kaputtes YAML und mehrere Dokumente", () => {
    expect(readForm("services: [a\n").locked).toMatch(/kein gültiges YAML/);
    expect(readForm("a: 1\n---\nb: 2\n").locked).toMatch(/mehrere YAML-Dokumente/);
    expect(readForm("- a\n").locked).toMatch(/nicht wie eine Compose-Datei/);
    expect(readForm("services: {web: {image: x}}\n").locked).toMatch(/besonders geschrieben/);
  });

  it("sperrt Dienste mit Vorlagen und Verweisen", () => {
    const text = `x-base: &base
  restart: always
services:
  a:
    <<: *base
    image: alpine
  b:
    extends: a
  c: &c
    image: alpine
  d: {image: alpine}
`;
    const [a, b, c, d] = readForm(text).services;
    expect(a.locked).toMatch(/<</);
    expect(b.locked).toMatch(/extends/);
    expect(c.locked).toMatch(/&c/);
    expect(d.locked).toMatch(/in einer Zeile/);
  });

  it("ausführlich geschriebene Ports nur zum Ansehen", () => {
    const text = "services:\n  a:\n    image: x\n    ports:\n      - target: 80\n        published: \"8080\"\n      - 9000:9000\n";
    expect(readForm(text).services[0].ports.items).toEqual([
      { value: 'target: 80 published: "8080"', editable: false },
      { value: "9000:9000", editable: true },
    ]);
  });

  it("Umgebung als Liste, Verweis und mehrzeiliges Image", () => {
    const text = "x: &i nginx\nservices:\n  a:\n    image: *i\n    environment:\n      - A=1\n      - B\n  b:\n    image: >\n      nginx\n";
    const [a, b] = readForm(text).services;
    expect(a.environment.list).toBe(true);
    expect(a.environment.entries).toEqual([{ key: "A", value: "1", editable: true }, { key: "B", value: "", editable: true }]);
    expect(a.imageLocked).toMatch(/\*i/);
    expect(b.imageLocked).toMatch(/Mehrzeilig/);
  });

  it("weist auf include hin", () => {
    expect(readForm("include:\n  - other.yaml\nservices:\n  a:\n    image: x\n").notes[0]).toMatch(/include/);
  });
});

describe("applyOp ändert nur die Stelle", () => {
  it("Image setzen, Kommentar und Rest bleiben", () => {
    expect(apply(FILE, { op: "set", service: "web", field: "image", value: "nginx:1.28" }))
      .toBe(FILE.replace("image: nginx:1.27   #", "image: nginx:1.28   #"));
  });

  it("behält einfache Anführungszeichen", () => {
    expect(apply(FILE, { op: "set", service: "db", field: "image", value: "postgres:17" }))
      .toBe(FILE.replace("'postgres:16'", "'postgres:17'"));
  });

  it("Neustart-Regel neu, ändern, entfernen", () => {
    const added = apply(FILE, { op: "set", service: "db", field: "restart", value: "always" });
    expect(added).toBe(FILE.replace(`data" ]\n`, `data" ]\n    restart: always\n`));
    expect(apply(added, { op: "unset", service: "db", field: "restart" })).toBe(FILE);
    expect(apply(FILE, { op: "set", service: "web", field: "restart", value: "no" }))
      .toBe(FILE.replace("restart: unless-stopped", 'restart: "no"'));
  });

  it("leerer Wert bekommt ein Leerzeichen", () => {
    const text = "services:\n  a:\n    image: x\n    restart:\n";
    expect(apply(text, { op: "set", service: "a", field: "restart", value: "always" })).toBe("services:\n  a:\n    image: x\n    restart: always\n");
  });

  it("Port hinzufügen, ändern, entfernen", () => {
    const added = apply(FILE, { op: "addItem", service: "web", field: "ports", value: "9000:9000" });
    expect(added).toBe(FILE.replace(`- "443:443"\n`, `- "443:443"\n      - "9000:9000"\n`));
    expect(apply(FILE, { op: "setItem", service: "web", field: "ports", index: 1, value: "8443:443" }))
      .toBe(FILE.replace(`"443:443"`, `"8443:443"`));
    expect(apply(FILE, { op: "removeItem", service: "web", field: "ports", index: 0 }))
      .toBe(FILE.replace(`      - "8080:80"   # Verwaltung\n`, ""));
  });

  it("letzter Eintrag weg: Schlüssel weg", () => {
    expect(apply(FILE, { op: "removeItem", service: "db", field: "volumes", index: 0 }))
      .toBe(FILE.replace(`    volumes: [ "db:/var/lib/postgresql/data" ]\n`, ""));
  });

  it("Liste in einer Zeile", () => {
    const added = apply(FILE, { op: "addItem", service: "db", field: "volumes", value: "./backup:/backup" });
    expect(added).toBe(FILE.replace(`data" ]`, `data", "./backup:/backup" ]`));
    expect(apply(added, { op: "removeItem", service: "db", field: "volumes", index: 1 })).toBe(FILE);
    expect(apply(added, { op: "removeItem", service: "db", field: "volumes", index: 0 }))
      .toBe(FILE.replace(`"db:/var/lib/postgresql/data" ]`, `"./backup:/backup" ]`));
  });

  it("neue Liste im Stil der Datei", () => {
    const flat = "services:\n  a:\n    image: x\n    environment:\n    - A=1\n";
    expect(apply(flat, { op: "addItem", service: "a", field: "ports", value: "80:80" }))
      .toBe(`${flat}    ports:\n    - "80:80"\n`);
    const four = "services:\n    a:\n        image: x\n";
    expect(apply(four, { op: "addItem", service: "a", field: "volumes", value: "data:/data" }))
      .toBe(`${four}        volumes:\n            - data:/data\n`);
  });

  it("Umgebung als Map", () => {
    expect(apply(FILE, { op: "setEnv", service: "web", key: "TZ", value: "UTC" })).toBe(FILE.replace("Europe/Berlin", "UTC"));
    expect(apply(FILE, { op: "setEnv", service: "web", key: "PORT", value: "8081" })).toBe(FILE.replace("PORT: 8080", "PORT: 8081"));
    expect(apply(FILE, { op: "setEnv", service: "web", key: "PORT", value: "yes" })).toBe(FILE.replace("PORT: 8080", 'PORT: "yes"'));
    expect(apply(FILE, { op: "setEnv", service: "web", key: "NEU", value: "1" }))
      .toBe(FILE.replace("PORT: 8080\n", 'PORT: 8080\n      NEU: "1"\n'));
    expect(apply(FILE, { op: "removeEnv", service: "web", key: "TZ" })).toBe(FILE.replace("      TZ: Europe/Berlin\n", ""));
    expect(apply(FILE, { op: "setEnv", service: "db", key: "PGDATA", value: "/data" }))
      .toBe(FILE.replace(`data" ]\n`, `data" ]\n    environment:\n      PGDATA: /data\n`));
  });

  it("Umgebung als Liste", () => {
    const text = "services:\n  a:\n    image: x\n    environment:\n      - A=1\n      - B\n";
    expect(apply(text, { op: "setEnv", service: "a", key: "B", value: "2" })).toBe(text.replace("- B\n", "- B=2\n"));
    expect(apply(text, { op: "setEnv", service: "a", key: "C", value: "x y" })).toBe(`${text}      - C=x y\n`);
    expect(apply(text, { op: "removeEnv", service: "a", key: "A" })).toBe(text.replace("      - A=1\n", ""));
  });

  it("Dienst hinzufügen, mit Leerzeile wie in der Datei", () => {
    expect(apply(FILE, { op: "addService", name: "cache", image: "redis:7" }))
      .toBe(FILE.replace(`data" ]\n`, `data" ]\n\n  cache:\n    image: redis:7\n`));
    expect(apply("", { op: "addService", name: "app", image: "nginx" })).toBe("services:\n  app:\n    image: nginx\n");
    expect(apply("services:\n", { op: "addService", name: "app", image: "nginx" })).toBe("services:\n  app:\n    image: nginx\n");
    expect(apply("name: x", { op: "addService", name: "app", image: "nginx" })).toBe("name: x\nservices:\n  app:\n    image: nginx\n");
  });

  it("Werte mit Sonderzeichen bekommen Anführungszeichen", () => {
    const text = "services:\n  a:\n    image: x\n";
    expect(apply(text, { op: "set", service: "a", field: "image", value: "x # y" })).toBe('services:\n  a:\n    image: "x # y"\n');
    expect(apply(text, { op: "set", service: "a", field: "image", value: "x " })).toBe('services:\n  a:\n    image: "x "\n');
    expect(apply(text, { op: "addItem", service: "a", field: "volumes", value: "22:22" })).toBe(`${text}    volumes:\n      - "22:22"\n`);
  });
});

describe("applyOp lehnt ab", () => {
  it("geteilte Stellen", () => {
    const text = "services:\n  a:\n    image: &img nginx\n  b:\n    image: *img\n";
    expect(failure(text, { op: "set", service: "a", field: "image", value: "x" })).toMatch(/nicht sicher/);
    expect(failure(text, { op: "set", service: "b", field: "image", value: "x" })).toMatch(/nicht sicher/);
  });

  it("gesperrte Dienste und unbekannte Namen", () => {
    const text = "x: &b\n  image: y\nservices:\n  a:\n    <<: *b\n";
    expect(failure(text, { op: "set", service: "a", field: "image", value: "x" })).toMatch(/nicht sicher/);
    expect(failure(FILE, { op: "set", service: "fehlt", field: "image", value: "x" })).toMatch(/nicht sicher/);
  });

  it("ungültige Namen", () => {
    expect(failure(FILE, { op: "addService", name: "web", image: "x" })).toMatch(/gibt es schon/);
    expect(failure(FILE, { op: "addService", name: "-x", image: "x" })).toMatch(/Buchstaben/);
    expect(failure(FILE, { op: "setEnv", service: "web", key: "A B", value: "1" })).toMatch(/Leerzeichen/);
  });

  it("mehrzeilige Werte", () => {
    const text = "services:\n  a:\n    image: >\n      nginx\n";
    expect(failure(text, { op: "set", service: "a", field: "image", value: "x" })).toMatch(/nicht sicher/);
  });
});

const ODD = [
  "",
  "services:\n",
  "services: ~\n",
  "services: {}\n",
  "services:\n  a:\n",
  "services:\n  a: ~\n",
  "services:\n  a:\n    image: x",
  "services:\n  a:\n    image: x\n    ports: []\n    environment: {}\n    volumes:\n",
  "services:\n  a:\n    image: x\n    ports: [\"1:1\",\n      \"2:2\"]\n    environment: {A: 1, B: two}\n",
  "services:\n  a:\n    image: !!str x\n    ports:\n    - - 1\n    environment:\n      ? A\n      : 1\n",
  "services:\n  \"a\":\n    image: x # k\n    # nur Kommentar\n\n\n  b:\n    image: y\n",
  "services:\n\t a:\n",
  "x: &a [1, 2]\nservices:\n  a:\n    image: x\n    ports: *a\n",
  "services:\r\n  a:\r\n    image: x\r\n",
  "%YAML 1.2\n---\nservices:\n  a:\n    image: x\n",
  "services:\n  a:\n    image: *x\n",
  "services:\n  a:\n    image: !reset x\n    ports: !override [\"1:1\"]\n    environment: !reset\n",
  "services: !reset\n  a:\n    image: x\n",
];
const OPS: FormOp[] = [
  { op: "set", service: "a", field: "image", value: "neu" },
  { op: "set", service: "a", field: "restart", value: "always" },
  { op: "unset", service: "a", field: "image" },
  { op: "addItem", service: "a", field: "ports", value: "9:9" },
  { op: "setItem", service: "a", field: "ports", index: 0, value: "8:8" },
  { op: "removeItem", service: "a", field: "ports", index: 0 },
  { op: "removeItem", service: "a", field: "ports", index: 5 },
  { op: "addItem", service: "a", field: "volumes", value: "d:/d" },
  { op: "setEnv", service: "a", key: "A", value: "2" },
  { op: "setEnv", service: "a", key: "C", value: "3" },
  { op: "removeEnv", service: "a", key: "A" },
  { op: "addService", name: "neu", image: "busybox" },
  { op: "set", service: "a", field: "image", value: "*x" },
  { op: "setEnv", service: "a", key: "B", value: "&y" },
  { op: "addService", name: "null", image: "*z" },
];

describe("ungewöhnliche Dateien", () => {
  it.each(ODD)("wirft nie und liefert nur gültiges YAML: %j", (text) => {
    expect(() => readForm(text)).not.toThrow();
    for (const op of OPS) {
      const out = applyOp(text, op);
      if ("text" in out && out.text !== text) expect(readForm(out.text).locked).toBeNull();
    }
  });
});

describe("Werte, die Compose anders liest", () => {
  // Mit Docker Compose 5.6 nachgeprüft: ohne Anführungszeichen wird daraus ein Datum, eine andere Zahl oder gar nichts.
  const TRICKY = ["2024-01-05", "2024-01-05T10:00:00Z", "2024-01-05 10:00:00", "1_000", "1_0.5", "1_e3", "<<", ".5", "+1", "-x", "0x1f", "1e3"];

  it.each(TRICKY)("%s kommt in Anführungszeichen", (value) => {
    const out = apply(FILE, { op: "setEnv", service: "web", key: "NEU", value });
    expect(out).toContain(`      NEU: ${JSON.stringify(value)}\n`);
  });

  it("eine ganze Zahl bleibt eine Zahl, andere Zahlen nicht", () => {
    expect(apply(FILE, { op: "setEnv", service: "web", key: "PORT", value: "9090" })).toContain("PORT: 9090\n");
    expect(apply(FILE, { op: "setEnv", service: "web", key: "PORT", value: "1_000" })).toContain('PORT: "1_000"\n');
  });

  it("ein * am Anfang wirft nicht", () => {
    for (const op of [
      { op: "set", service: "web", field: "image", value: "*x" },
      { op: "setEnv", service: "web", key: "PW", value: "*geheim" },
      { op: "addItem", service: "web", field: "volumes", value: "*a:/b" },
      { op: "addService", name: "neu", image: "*x" },
    ] as FormOp[]) {
      const out = apply(FILE, op);
      expect(out).toMatch(/"\*/);
    }
  });

  it("zeigt den Wert, wie er in der Datei steht", () => {
    const form = readForm("services:\n  a:\n    image: x\n    environment:\n      V: 1.10\n      O: 0o17\n");
    expect(form.services[0].environment.entries.map((e) => e.value)).toEqual(["1.10", "0o17"]);
  });

  it("Namen, die YAML nicht als Text liest", () => {
    for (const name of ["null", "true", "false", "123", "2024-01-05"]) {
      expect(failure(FILE, { op: "addService", name, image: "x" })).toMatch(/nicht als Text/);
    }
    expect(failure(FILE, { op: "setEnv", service: "web", key: "true", value: "1" })).toMatch(/nicht als Text/);
  });
});

describe("Markierungen wie !reset und !override", () => {
  const TAGGED = "services:\n  a:\n    image: x\n    restart: !reset always\n    ports: !override\n      - \"80:80\"\n    environment: !reset {}\n";

  it("sind gesperrt", () => {
    const [svc] = readForm(TAGGED).services;
    expect(svc.restartLocked).toMatch(/!reset/);
    expect(svc.ports.locked).toMatch(/!override/);
    expect(svc.environment.locked).toMatch(/!reset/);
  });

  it("und lassen sich nicht ändern", () => {
    expect(failure(TAGGED, { op: "set", service: "a", field: "restart", value: "no" })).toMatch(/nicht sicher/);
    expect(failure(TAGGED, { op: "unset", service: "a", field: "restart" })).toMatch(/nicht sicher/);
    expect(failure(TAGGED, { op: "removeItem", service: "a", field: "ports", index: 0 })).toMatch(/nicht sicher/);
    expect(failure(TAGGED, { op: "addItem", service: "a", field: "ports", value: "1:1" })).toMatch(/nicht sicher/);
    expect(failure(TAGGED, { op: "setEnv", service: "a", key: "A", value: "1" })).toMatch(/nicht sicher/);
  });

  it("kaputtes YAML nennt die Zeile", () => {
    expect(readForm("services:\n  a:\n    image: x\n   b: [\n").locked).toMatch(/kein gültiges YAML \(Zeile \d+\)/);
  });
});
