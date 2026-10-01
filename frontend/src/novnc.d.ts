// @types/novnc__novnc deklariert noch den alten CommonJS-Pfad `@novnc/novnc/lib/rfb`;
// seit noVNC 1.4 exportiert das Paket die RFB-Klasse ueber seinen Wurzelpfad
// (`exports: "./core/rfb.js"`). Dieselben Typen, unter dem Namen, den der Import
// tatsaechlich benutzt.
declare module "@novnc/novnc" {
  import RFB from "@novnc/novnc/lib/rfb";

  export default RFB;
}
