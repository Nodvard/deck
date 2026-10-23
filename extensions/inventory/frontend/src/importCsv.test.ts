import { describe, expect, it } from "vitest";

import { MAX_IMPORT_ROWS, decodeCsv, parseCsv, parseDate, parsePriceCents, rowsFromCsv } from "./importCsv";

describe("CSV einlesen", () => {
  it("Felder mit Anführungszeichen, Trennzeichen und Zeilenumbrüchen", () => {
    expect(parseCsv('a,b\n"x, y","z ""q"""\n')).toEqual([["a", "b"], ["x, y", 'z "q"']]);
    expect(parseCsv("﻿Name;Menge\r\nKabel;3\r\n\r\n")).toEqual([["Name", "Menge"], ["Kabel", "3"]]);
    expect(parseCsv('n,notiz\nA,"zwei\nZeilen"\n')).toEqual([["n", "notiz"], ["A", "zwei\nZeilen"]]);
  });

  it("Datum und Preis in den üblichen Schreibweisen", () => {
    expect(parseDate("2024-03-02T10:11:12Z")).toBe("2024-03-02");
    expect(parseDate("2.3.2024")).toBe("2024-03-02");
    expect(parseDate("3/2/2024")).toBe("2024-03-02");
    expect(parseDate("gestern")).toBeNull();
    expect(parsePriceCents("24.99")).toBe(2499);
    expect(parsePriceCents("24,99 €")).toBe(2499);
    expect(parsePriceCents("€ 1.234,50")).toBe(123450);
    expect(parsePriceCents("1,234.50 EUR")).toBe(123450);
    expect(parsePriceCents("")).toBeNull();
  });

  it("Amazon-Bestellverlauf: Produktname, Datum, Preis, Menge; ASIN wird zum Link, Storniertes fällt weg", () => {
    const csv = [
      '"Website","Order ID","Order Date","Currency","Unit Price","Quantity","ASIN","Order Status","Product Name","Item Serial Number"',
      '"Amazon.de","302-1","2024-03-02T10:11:12Z","EUR","24.99","2","B07ABCDEF1","Closed","TP-Link TL-SG108 Switch, 8 Port","Not Available"',
      '"Amazon.de","302-2","2024-05-01T08:00:00Z","EUR","89.00","1","B07ABCDEF2","Cancelled","Abgebrochen","Not Available"',
    ].join("\n");
    const result = rowsFromCsv(csv);
    expect(result.source).toBe("amazon");
    expect(result.skipped).toBe(1);
    expect(result.rows).toEqual([{
      name: "TP-Link TL-SG108 Switch, 8 Port", quantity: 2, purchase_date: "2024-03-02", purchase_price_cents: 2499,
      warranty_until: null, description: null, manufacturer: null, model: null, serial_number: null,
      product_url: "https://www.amazon.de/dp/B07ABCDEF1", notes: null, category: null, location: null,
    }]);
    expect(result.notes).toEqual([]);
  });

  it("der eigene Export lässt sich wieder einlesen", () => {
    const csv = "﻿Name;Kategorie;Standort;Menge;Kaufdatum;Preis (EUR);Garantie bis;Beschreibung;Notizen;Hersteller;Modell;Seriennummer;Produktlink\r\n"
      + 'Festplatte;Server;Keller;2;2026-09-20;119,90;2029-09-20;;"Regal ""oben""";WD;Red Plus 4TB;WX1;https://example.com/wd\r\n';
    const result = rowsFromCsv(csv);
    expect(result.source).toBe("inventar");
    expect(result.rows[0]).toMatchObject({
      name: "Festplatte", quantity: 2, purchase_date: "2026-09-20", purchase_price_cents: 11990, manufacturer: "WD", model: "Red Plus 4TB",
      serial_number: "WX1", product_url: "https://example.com/wd", notes: 'Regal "oben"', category: "Server", location: "Keller",
      warranty_until: "2029-09-20", description: null,
    });
  });

  it("ohne Namensspalte nichts, unsichere Links nicht", () => {
    expect(rowsFromCsv("Preis,Menge\n1,2\n").rows).toEqual([]);
    expect(rowsFromCsv("Name,Link\nA,javascript:alert(1)\n").rows[0].product_url).toBeNull();
  });

  it("Trennzeichen: Tabulator, „sep=“-Zeile, Kommas in Anführungszeichen zählen nicht", () => {
    expect(parseCsv("Name\tMenge\nKabel\t3\n")).toEqual([["Name", "Menge"], ["Kabel", "3"]]);
    expect(parseCsv("sep=,\nName,Preis\nA,\"1,5\"\n")).toEqual([["Name", "Preis"], ["A", "1,5"]]);
    // Die Kopfzeile hat in Anführungszeichen mehr Kommas als Semikolons außerhalb: trotzdem Semikolon.
    expect(parseCsv('"Name, lang";"Notiz, lang"\nA;B\n')).toEqual([["Name, lang", "Notiz, lang"], ["A", "B"]]);
    // Ein Anführungszeichen mitten im Text ist ein Zoll-Zeichen, kein Feldanfang.
    expect(parseCsv('Name;Menge\nMonitor 27" IPS;1\n')).toEqual([["Name", "Menge"], ['Monitor 27" IPS', "1"]]);
  });

  it("Windows-Zeichensatz (Excel) wird erkannt, UTF-8 bleibt UTF-8", () => {
    const latin1 = new Uint8Array([0x47, 0x72, 0xf6, 0xdf, 0x65]); // „Größe“ in windows-1252
    expect(decodeCsv(latin1.buffer)).toBe("Größe");
    expect(decodeCsv(new TextEncoder().encode("Größe").buffer as ArrayBuffer)).toBe("Größe");
  });

  it("Datum: Tag und Monat richtig herum, zweistellige Jahre, unmögliche Tage nicht", () => {
    expect(parseDate("25/12/2024")).toBe("2024-12-25");
    expect(parseDate("12/25/2024")).toBe("2024-12-25");
    expect(parseDate("2.3.24")).toBe("2024-03-02");
    expect(parseDate("31.02.2024")).toBeNull();
    expect(parseDate("2024-13-01")).toBeNull();
  });

  it("Preis: Tausender ohne Cent, Gutschriften und fremde Währung nicht", () => {
    expect(parsePriceCents("1.234")).toBe(123400);
    expect(parsePriceCents("1.234.567")).toBe(123456700);
    expect(parsePriceCents("0.500")).toBe(50);
    expect(parsePriceCents("1,5")).toBe(150);
    expect(parsePriceCents("-12,99")).toBeNull();
    expect(parsePriceCents("€ -12,99")).toBeNull();
    expect(parsePriceCents("(12.99)")).toBeNull();
    const result = rowsFromCsv("Name,Price,Currency\nA,12.99,USD\nB,$5.00,\nC,3.50,EUR\n");
    expect(result.rows.map((r) => r.purchase_price_cents)).toEqual([null, null, 350]);
    expect(result.notes).toEqual(["2 Preise in USD: nicht übernommen (das Inventar rechnet in Euro)."]);
  });

  it("eine Summe der Zeile wird durch die Menge geteilt, ein Stückpreis nicht", () => {
    const result = rowsFromCsv("Name;Menge;Gesamtpreis\nKabel;4;20,00\n");
    expect(result.rows[0].purchase_price_cents).toBe(500);
    expect(rowsFromCsv("Name;Menge;Einzelpreis\nKabel;4;20,00\n").rows[0].purchase_price_cents).toBe(2000);
  });

  it("älterer Amazon-Export: ASIN/ISBN, Stückpreis, Shop aus der Spalte Website", () => {
    const csv = [
      "Order Date,Title,ASIN/ISBN,Website,Purchase Price Per Unit,Quantity",
      "01/15/2019,USB-Stick,b00abcdefg,Amazon.co.uk,£9.99,1",
      "01/16/2019,Kabel,B00ABCDEF9,Amazon.de,\"7,49 €\",2",
    ].join("\n");
    const result = rowsFromCsv(csv);
    expect(result.source).toBe("amazon");
    expect(result.rows.map((r) => [r.product_url, r.purchase_price_cents, r.purchase_date])).toEqual([
      ["https://www.amazon.co.uk/dp/B00ABCDEFG", null, "2019-01-15"],
      ["https://www.amazon.de/dp/B00ABCDEF9", 749, "2019-01-16"],
    ]);
    expect(result.notes).toEqual(["1 Preis in GBP: nicht übernommen (das Inventar rechnet in Euro)."]);
  });

  it("Striche zählen als leer, Links mit Leerzeichen oder überlang nicht", () => {
    const result = rowsFromCsv(`Name,Hersteller,Seriennummer,Link\nA,–,-,https://example.com/a b\nB,x,y,https://example.com/${"a".repeat(2000)}\n`);
    expect(result.rows.map((r) => [r.manufacturer, r.serial_number, r.product_url])).toEqual([[null, null, null], ["x", "y", null]]);
  });

  it("sehr lange Listen werden abgeschnitten und sagen es", () => {
    const lines = ["Name", ...Array.from({ length: MAX_IMPORT_ROWS + 3 }, (_, i) => `Teil ${i}`)].join("\n");
    const result = rowsFromCsv(lines);
    expect(result.rows).toHaveLength(MAX_IMPORT_ROWS);
    expect(result.notes).toEqual([`Die Datei hat mehr als ${MAX_IMPORT_ROWS} Einträge: angezeigt werden die ersten ${MAX_IMPORT_ROWS}, 3 weitere nicht.`]);
  });

  it("Randfälle aus der Prüfung: Jahrhundert, Tag/Monat je Datei, fremde Währung aus der Spalte", () => {
    expect(parseDate("01.01.99")).toBe("1999-01-01");
    expect(parseDate("3/15/98")).toBe("1998-03-15");
    // UK-Datei: 25/03 verrät „Tag zuerst“, dann ist 10/03 der 10. März und nicht der 3. Oktober.
    const uk = rowsFromCsv("Name,Order Date\nA,10/03/2024\nB,25/03/2024\n");
    expect(uk.rows.map((r) => r.purchase_date)).toEqual(["2024-03-10", "2024-03-25"]);
    const us = rowsFromCsv("Name,Order Date\nA,10/03/2024\nB,03/25/2024\n");
    expect(us.rows.map((r) => r.purchase_date)).toEqual(["2024-10-03", "2024-03-25"]);

    const foreign = rowsFromCsv('Name,Unit Price,Currency\nA,"1499.00",INR\nB,"R$ 12,00",\nC,"12,99 EUR.",\n');
    expect(foreign.rows.map((r) => r.purchase_price_cents)).toEqual([null, null, 1299]);
    expect(foreign.notes).toEqual([
      "1 Preis in INR: nicht übernommen (das Inventar rechnet in Euro).",
      "1 Preis in BRL: nicht übernommen (das Inventar rechnet in Euro).",
    ]);
  });

  it("Randfälle aus der Prüfung: Minuszeichen, Riesenmengen, Links, Emoji, Formeln", () => {
    expect(parsePriceCents("−12,99")).toBeNull();
    expect(parsePriceCents("99999999999999,00")).toBeNull();
    const result = rowsFromCsv([
      "Name,Menge,Link",
      "A,99999999999999999999,http://:80",
      `${"x".repeat(299)}😀 Rest,0,https://a.b/\u0001x`,
      "'=1+1,2,https://example.com/ok",
    ].join("\n"));
    expect(result.rows.map((r) => [r.quantity, r.product_url])).toEqual([[1, null], [1, null], [2, "https://example.com/ok"]]);
    expect(result.rows[1].name).toBe(`${"x".repeat(299)}😀`);
    expect(result.rows[2].name).toBe("=1+1");
  });

  it("Randfälle aus der Prüfung: Zeichensatz, Trennzeichen, Leerzeichen vor Anführungszeichen, offenes Anführungszeichen", () => {
    // Gültiges UTF-8 mit einem echten Ersatzzeichen bleibt UTF-8.
    expect(decodeCsv(new TextEncoder().encode("Über � Zeichen").buffer as ArrayBuffer)).toBe("Über � Zeichen");
    expect(parseCsv("Name\tPreis, brutto\nKabel\t5,00\n")).toEqual([["Name", "Preis, brutto"], ["Kabel", "5,00"]]);
    expect(parseCsv('a,b\nA, "b, c"\n')).toEqual([["a", "b"], ["A", "b, c"]]);
    const open = rowsFromCsv('Name,Notiz\nA,"offen\nB,x\n');
    expect(open.notes).toEqual(["In der Datei fehlt ein schließendes Anführungszeichen: die letzten Einträge sind womöglich unvollständig. Prüfe sie vor dem Übernehmen."]);
  });

  it("Garantie in der Zukunft, unregelmäßige Zeilen, Mengen mit Zusatz, eine Spalte", () => {
    const warranty = rowsFromCsv("Name;Kaufdatum;Garantie bis\nTV;01.06.26;01.06.29\n");
    expect([warranty.rows[0].purchase_date, warranty.rows[0].warranty_until]).toEqual(["2026-06-01", "2029-06-01"]);
    // Die Kopfzeile hat mehr Semikolons als Kommas, auch wenn eine Zeile eine Spalte weniger hat.
    const irregular = rowsFromCsv("Name;Preis, brutto;Notiz\nKabel;5,00;x\nMaus;6,00\nTastatur;7,00;y\n");
    expect(irregular.rows.map((r) => r.name)).toEqual(["Kabel", "Maus", "Tastatur"]);
    const quantities = rowsFromCsv("Name;Menge\nA;2.0\nB;2,0\nC;3 Stk.\nD;4x\nE;2.5\n");
    expect(quantities.rows.map((r) => r.quantity)).toEqual([2, 2, 3, 4, 1]);
    // Nur eine Spalte: ein Komma im Namen trennt nichts.
    expect(rowsFromCsv("Name\nKabel, blau\n").rows.map((r) => r.name)).toEqual(["Kabel, blau"]);
  });

  it("Währungsspalte nur als Code, Dollar-Varianten, Preise mit Zusätzen", () => {
    const result = rowsFromCsv([
      "Name,Unit Price,Currency",
      "A,12.99,Euro",
      "B,12.99,Not Applicable",
      "C,A$12,",
      "D,CA$12,",
      '"E","12,99 EUR (inkl. 19% MwSt.)",',
      '"F","1 234,50 €",',
      "G,.5,",
    ].join("\n"));
    expect(result.rows.map((r) => r.purchase_price_cents)).toEqual([1299, 1299, null, null, 1299, 123450, 50]);
    expect(result.notes).toEqual([
      "1 Preis in AUD: nicht übernommen (das Inventar rechnet in Euro).",
      "1 Preis in CAD: nicht übernommen (das Inventar rechnet in Euro).",
    ]);
    expect(parsePriceCents("–12,99")).toBeNull();
    expect(parsePriceCents("12,99–")).toBeNull();
  });

  it("Preise und Mengen nur bis zu den Grenzen des Servers", () => {
    const result = rowsFromCsv("Name,Preis,Menge\nA,\"10.000.000,00\",100000\nB,\"10.000.000,01\",1\nC,5,100001\n");
    expect(result.rows.map((r) => [r.purchase_price_cents, r.quantity])).toEqual([
      [1_000_000_000, 100000], [null, 1], [500, 1],
    ]);
  });

  it("Links nur so, wie der Server sie nimmt", () => {
    const result = rowsFromCsv("Name,Link\nA,https:example.com\nB,http:/example.com\nC,https:///example.com\nD,https://nutzer:" + "geheim@example.com/\nE,https://example.com/x\nF,https://@example.com/\n");
    expect(result.rows.map((r) => r.product_url)).toEqual([null, null, null, null, "https://example.com/x", null]);
  });
});
