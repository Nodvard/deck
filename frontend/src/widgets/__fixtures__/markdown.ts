import type { MarkdownView } from "../types";

export const view: MarkdownView = {
  kind: "markdown",
  content_field: "content",
};

export const data = {
  // Deckt auch die
  // Faehigkeiten ab, die der vorherige handgerollte Parser nicht kannte (Liste,
  // Codeblock, Tabelle) -- nicht nur die urspruenglichen Ueberschrift/fett/XSS-Faelle.
  content:
    "# Status\n\nAlles **gut**, <script>alert(1)</script>\n\n" +
    "- Punkt eins\n- Punkt zwei\n\n" +
    "```\nkonsole --version\n```\n\n" +
    "| Dienst | Zustand |\n|---|---|\n| pve2 | ok |\n",
};

export const expectContains = ["Status", "gut", "Punkt eins", "konsole --version", "pve2"];
export const expectNotContains = ["<script>"];
