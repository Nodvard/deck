/** Zeilenweiser Unterschied zweier Texte, für die Ansicht vor dem Speichern. */
export interface DiffLine { kind: " " | "+" | "-"; text: string }
export interface Diff { added: number; removed: number; lines: DiffLine[] | null }

// Darüber würde die Tabelle im Browser zu groß; dann gibt es nur die Zahlen.
const MAX_CELLS = 2_000_000;

function counts(a: string[], b: string[]): { added: number; removed: number } {
  const left = new Map<string, number>();
  for (const line of a) left.set(line, (left.get(line) ?? 0) + 1);
  let added = 0;
  for (const line of b) {
    const n = left.get(line) ?? 0;
    if (n > 0) left.set(line, n - 1);
    else added += 1;
  }
  return { added, removed: a.length - (b.length - added) };
}

export function lineDiff(before: string, after: string): Diff {
  const a = before.split("\n");
  const b = after.split("\n");
  let start = 0;
  while (start < a.length && start < b.length && a[start] === b[start]) start += 1;
  let endA = a.length;
  let endB = b.length;
  while (endA > start && endB > start && a[endA - 1] === b[endB - 1]) {
    endA -= 1;
    endB -= 1;
  }
  const midA = a.slice(start, endA);
  const midB = b.slice(start, endB);
  const n = midA.length;
  const m = midB.length;
  if ((n + 1) * (m + 1) > MAX_CELLS) {
    const found = counts(midA, midB);
    // Nur umgestellt: Zählen nach Inhalt ergibt nichts, deshalb die Zeilen, die nicht mehr an ihrer Stelle stehen.
    const moved = found.added || found.removed ? 0 : midA.filter((line, i) => line !== midB[i]).length;
    return { added: found.added || moved, removed: found.removed || moved, lines: null };
  }

  // Längste gemeinsame Teilfolge, von hinten gefüllt, damit der Weg von vorn gelesen werden kann.
  const width = m + 1;
  const table = new Uint32Array((n + 1) * width);
  for (let i = n - 1; i >= 0; i -= 1) {
    for (let j = m - 1; j >= 0; j -= 1) {
      table[i * width + j] = midA[i] === midB[j]
        ? table[(i + 1) * width + j + 1] + 1
        : Math.max(table[(i + 1) * width + j], table[i * width + j + 1]);
    }
  }
  const lines: DiffLine[] = a.slice(0, start).map((text) => ({ kind: " ", text }));
  let i = 0;
  let j = 0;
  while (i < n || j < m) {
    if (i < n && j < m && midA[i] === midB[j]) {
      lines.push({ kind: " ", text: midA[i] });
      i += 1;
      j += 1;
    } else if (i < n && (j === m || table[(i + 1) * width + j] >= table[i * width + j + 1])) {
      lines.push({ kind: "-", text: midA[i] });
      i += 1;
    } else {
      lines.push({ kind: "+", text: midB[j] });
      j += 1;
    }
  }
  for (const text of a.slice(endA)) lines.push({ kind: " ", text });
  const added = lines.filter((l) => l.kind === "+").length;
  return { added, removed: lines.filter((l) => l.kind === "-").length, lines };
}

/** Nur die geänderten Zeilen mit etwas Umgebung; `null` steht für ausgelassene Zeilen. */
export function hunks(lines: DiffLine[], context = 2): (DiffLine | null)[] {
  const keep = new Array<boolean>(lines.length).fill(false);
  lines.forEach((line, index) => {
    if (line.kind === " ") return;
    for (let k = Math.max(0, index - context); k <= Math.min(lines.length - 1, index + context); k += 1) keep[k] = true;
  });
  const out: (DiffLine | null)[] = [];
  lines.forEach((line, index) => {
    if (keep[index]) out.push(line);
    else if (out.length === 0 || out[out.length - 1] !== null) out.push(null);
  });
  return out;
}
