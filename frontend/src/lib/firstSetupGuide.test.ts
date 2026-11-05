import { describe, expect, it } from "vitest";

const nodeFs = "node:" + "fs";
const nodeUrl = "node:" + "url";
const fs = (await import(/* @vite-ignore */ nodeFs)) as {
  readFileSync: (file: string, encoding: "utf-8") => string;
};
const { fileURLToPath } = (await import(/* @vite-ignore */ nodeUrl)) as {
  fileURLToPath: (url: string) => string;
};

describe.each([
  {
    doc: "docs/11-ERST-EINRICHTUNG.md",
    heading: "## 4. Erweiterungen einschalten",
    advice: [
      "Das Einschalten allein ergänzt keine Rechte auf dem Server",
      "Docker-Gruppe und Hilfsskripte",
      "für jeden betroffenen Server mit bestehendem Schlüsselzugang",
      "neu angezeigten Einrichtungsbefehl erneut aus",
      "[3.3](#33-einrichtungsbefehl-auf-dem-server-ausführen)",
      "Prüfe danach die Verbindung erneut",
      "[3.4](#34-verbindung-prüfen)",
    ],
  },
  {
    doc: "docs/en/FIRST-SETUP.md",
    heading: "## 4. Enable extensions",
    advice: [
      "Switching on a module alone does not add rights on the server",
      "docker group and helper scripts",
      "for each affected server with an existing key login",
      "run the newly displayed setup command again",
      "[3.3](#33-run-the-setup-command-on-the-server)",
      "Then check the connection again",
      "[3.4](#34-check-the-connection)",
    ],
  },
])("$doc", ({ doc, heading, advice }) => {
  it.each([
    { format: "LF", lineEnding: "\n" },
    { format: "CRLF", lineEnding: "\r\n" },
  ])("erklärt das Ergänzen der Serverrechte nach der Modulaktivierung mit $format", ({ lineEnding }) => {
    const directory = "../../../";
    const root = new URL(directory, import.meta.url);
    const text = fs.readFileSync(fileURLToPath(new URL(doc, root).href), "utf-8")
      .replace(/\r?\n/g, lineEnding);
    const section = text.replace(/\r\n/g, "\n").split(`${heading}\n`)[1]?.split("\n### ")[0];
    expect(section).toBeDefined();
    const normalized = section!.split(/\s+/).join(" ");
    for (const instruction of advice) expect(normalized).toContain(instruction);
  });
});
